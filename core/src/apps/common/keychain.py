import sys
from typing import TYPE_CHECKING

from trezor import utils, wire
from trezor.crypto import bip32

from . import paths, safety_checks
from .seed import Slip21Node, get_seed

if TYPE_CHECKING:
    from typing import (
        Any,
        Awaitable,
        Callable,
        Iterable,
        TypeVar,
    )
    from typing_extensions import Protocol

    from trezor.protobuf import MessageType

    T = TypeVar("T")

    class NodeProtocol(Protocol[paths.PathType]):
        def derive_path(self, path: paths.PathType) -> None:
            ...

        def clone(self: T) -> T:
            ...

        def __del__(self) -> None:
            ...

    NodeType = TypeVar("NodeType", bound=NodeProtocol)

    MsgIn = TypeVar("MsgIn", bound=MessageType)
    MsgOut = TypeVar("MsgOut", bound=MessageType)

    Handler = Callable[[wire.Context, MsgIn], Awaitable[MsgOut]]
    HandlerWithKeychain = Callable[[wire.Context, MsgIn, "Keychain"], Awaitable[MsgOut]]

    class Deletable(Protocol):
        def __del__(self) -> None:
            ...


FORBIDDEN_KEY_PATH = wire.DataError("Forbidden key path")


class LRUCache:
    def __init__(self, size: int) -> None:
        self.size = size
        self.cache_keys: list[Any] = []
        self.cache: dict[Any, Deletable] = {}

    def insert(self, key: Any, value: Deletable) -> None:
        if key in self.cache_keys:
            self.cache_keys.remove(key)
        self.cache_keys.insert(0, key)
        self.cache[key] = value

        if len(self.cache_keys) > self.size:
            dropped_key = self.cache_keys.pop()
            self.cache[dropped_key].__del__()
            del self.cache[dropped_key]

    def get(self, key: Any) -> Any:
        if key not in self.cache:
            return None

        self.cache_keys.remove(key)
        self.cache_keys.insert(0, key)
        return self.cache[key]

    def __del__(self) -> None:
        for value in self.cache.values():
            value.__del__()
        self.cache.clear()
        self.cache_keys.clear()
        del self.cache


class Keychain:
    def __init__(
        self,
        seed: bytes,
        curve: str,
        schemas: Iterable[paths.PathSchemaType],
        slip21_namespaces: Iterable[paths.Slip21Path] = (),
    ) -> None:
        self.seed = seed
        self.curve = curve
        self.schemas = tuple(schemas)
        self.slip21_namespaces = tuple(slip21_namespaces)

        self._cache = LRUCache(10)
        self._root_fingerprint: int | None = None

    def __del__(self) -> None:
        self._cache.__del__()
        del self._cache
        del self.seed

    def verify_path(self, path: paths.Bip32Path, force_strict: bool = True) -> None:
        if "ed25519" in self.curve and not paths.path_is_hardened(path):
            raise wire.DataError("Non-hardened paths unsupported on Ed25519")

        if not safety_checks.is_strict() and not force_strict:
            return

        if self.is_in_keychain(path):
            return

        raise FORBIDDEN_KEY_PATH

    def is_in_keychain(self, path: paths.Bip32Path) -> bool:
        return any(schema.match(path) for schema in self.schemas)

    def _derive_with_cache(
        self,
        prefix_len: int,
        path: paths.PathType,
        new_root: Callable[[], NodeType],
    ) -> NodeType:
        if not utils.USE_THD89:
            cached_prefix = tuple(path[:prefix_len])
            cached_root: NodeType | None = self._cache.get(cached_prefix)
            if cached_root is None:
                cached_root = new_root()
                cached_root.derive_path(cached_prefix)
                self._cache.insert(cached_prefix, cached_root)

            node = cached_root.clone()
            node.derive_path(path[prefix_len:])
        else:
            node = bip32.HDNode(
                depth=0,
                fingerprint=0,
                child_num=0,
                chain_code=bytearray(32),
                public_key=bytearray(33),
                curve_name=self.curve,
            )
            node.derive_path(path)  # type: ignore[Argument of type "PathType@_derive_with_cache" cannot be assigned to parameter "path" of type "Sequence[int]" in function "derive_path"]
        return node  # type: ignore[Expression of type "NodeType@_derive_with_cache | NodeProtocol[Unknown]* | HDNode" cannot be assigned to return type "NodeType@_derive_with_cache"]

    def root_fingerprint(self) -> int:
        if self._root_fingerprint is None:
            # derive m/0' to obtain root_fingerprint
            n = self._derive_with_cache(
                prefix_len=0,
                path=[0 | paths.HARDENED],
                new_root=lambda: bip32.from_seed(self.seed, self.curve),  # type: ignore[Argument of type "() -> HDNode" cannot be assigned to parameter "new_root" of type "() -> NodeType@_derive_with_cache" in function "_derive_with_cache"]
            )
            self._root_fingerprint = n.fingerprint()  # type: ignore[Cannot access member "fingerprint" for type "NodeProtocol[Unknown]*"]
        return self._root_fingerprint

    def derive(self, path: paths.Bip32Path, force_strict: bool = True) -> bip32.HDNode:
        self.verify_path(path, force_strict)
        return self._derive_with_cache(  # type: ignore[Expression of type "NodeType@_derive_with_cache" cannot be assigned to return type "HDNode"]
            prefix_len=3,
            path=path,
            new_root=lambda: bip32.from_seed(self.seed, self.curve),  # type: ignore[Argument of type "() -> HDNode" cannot be assigned to parameter "new_root" of type "() -> NodeType@_derive_with_cache" in function "_derive_with_cache"]
        )

    def derive_slip21(self, path: paths.Slip21Path) -> Slip21Node:
        if safety_checks.is_strict() and not any(
            ns == path[: len(ns)] for ns in self.slip21_namespaces
        ):
            raise FORBIDDEN_KEY_PATH
        if utils.USE_THD89:
            node = Slip21Node(seed=b"\x00" * 32)
            node.derive_path(path)
            return node
        else:
            return self._derive_with_cache(
                prefix_len=1,
                path=path,
                new_root=lambda: Slip21Node(seed=self.seed),
            )

    def __enter__(self) -> "Keychain":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, tb: Any) -> None:
        self.__del__()


async def get_keychain(
    ctx: wire.Context,
    curve: str,
    schemas: Iterable[paths.PathSchemaType],
    slip21_namespaces: Iterable[paths.Slip21Path] = (),
) -> Keychain:
    if utils.USE_THD89:
        await get_seed(ctx)
        keychain = Keychain(b"", curve, schemas, slip21_namespaces)
    else:
        seed = await get_seed(ctx)
        keychain = Keychain(seed, curve, schemas, slip21_namespaces)
    return keychain


def with_slip44_keychain(
    *patterns: str,
    slip44_id: int,
    curve: str = "secp256k1",
    allow_testnet: bool = True,
) -> Callable[[HandlerWithKeychain[MsgIn, MsgOut]], Handler[MsgIn, MsgOut]]:
    if not patterns:
        raise ValueError  # specify a pattern

    if allow_testnet:
        slip44_ids: int | tuple[int, int] = (slip44_id, 1)
    else:
        slip44_ids = slip44_id

    schemas = []
    for pattern in patterns:
        schemas.append(paths.PathSchema.parse(pattern=pattern, slip44_id=slip44_ids))
    schemas = [s.copy() for s in schemas]

    def decorator(func: HandlerWithKeychain[MsgIn, MsgOut]) -> Handler[MsgIn, MsgOut]:
        async def wrapper(ctx: wire.Context, msg: MsgIn) -> MsgOut:
            keychain = await get_keychain(ctx, curve, schemas)
            with keychain:
                return await func(ctx, msg, keychain)

        return wrapper

    return decorator


def auto_keychain(
    modname: str, allow_testnet: bool = True
) -> Callable[[HandlerWithKeychain[MsgIn, MsgOut]], Handler[MsgIn, MsgOut]]:
    rdot = modname.rfind(".")
    parent_modname = modname[:rdot]
    parent_module = sys.modules[parent_modname]

    pattern = getattr(parent_module, "PATTERN")
    curve = getattr(parent_module, "CURVE")
    slip44_id = getattr(parent_module, "SLIP44_ID")
    pattern = pattern if isinstance(pattern, tuple) else (pattern,)
    return with_slip44_keychain(
        *pattern, slip44_id=slip44_id, curve=curve, allow_testnet=allow_testnet
    )
