#define VERSION_MAJOR 2
#define VERSION_MINOR 99
#define VERSION_PATCH 99
#define VERSION_BUILD 99

#define FIX_VERSION_MAJOR 2
#define FIX_VERSION_MINOR 99
#define FIX_VERSION_PATCH 99
#define FIX_VERSION_BUILD 99

#define ONEKEY_VERSION_MAJOR 4
#define ONEKEY_VERSION_MINOR 9
#define ONEKEY_VERSION_PATCH 3
#define ONEKEY_VERSION_BUILD 0

#define _STR(X) #X
#define VERSTR(X) _STR(X)

#define ONEKEY_VERSION         \
  VERSTR(ONEKEY_VERSION_MAJOR) \
  "." VERSTR(ONEKEY_VERSION_MINOR) "." VERSTR(ONEKEY_VERSION_PATCH)
