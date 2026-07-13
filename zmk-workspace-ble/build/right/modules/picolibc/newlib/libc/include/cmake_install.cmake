# Install script for directory: /home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "/usr/local")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "TRUE")
endif()

# Set path to fallback-tool for dependency-resolution.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "/nix/store/mmkh2v78liwvll9ikdamv3iqwy5drm1g-gcc-arm-embedded-15.2.rel1/bin/arm-none-eabi-objdump")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/modules/picolibc/newlib/libc/include/sys/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/modules/picolibc/newlib/libc/include/machine/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/modules/picolibc/newlib/libc/include/ssp/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/modules/picolibc/newlib/libc/include/rpc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/modules/picolibc/newlib/libc/include/arpa/cmake_install.cmake")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include" TYPE FILE FILES
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/alloca.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/argz.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/ar.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/assert.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/byteswap.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/cpio.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/ctype.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/devctl.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/dirent.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/elf.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/endian.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/envlock.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/envz.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/errno.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/fastmath.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/fcntl.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/fenv.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/fnmatch.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/getopt.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/glob.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/grp.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/iconv.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/ieeefp.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/inttypes.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/langinfo.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/libgen.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/limits.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/locale.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/malloc.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/math.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/memory.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/newlib.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/paths.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/picotls.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/pwd.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/regdef.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/regex.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/sched.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/search.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/setjmp.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/signal.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/spawn.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/stdint.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/stdnoreturn.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/stdlib.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/string.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/strings.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/_syslist.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/tar.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/termios.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/threads.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/time.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/unctrl.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/unistd.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/utime.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/utmp.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/wchar.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/wctype.h"
    "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/wordexp.h"
    )
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include" TYPE FILE FILES "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/modules/lib/picolibc/newlib/libc/include/complex.h")
endif()

string(REPLACE ";" "\n" CMAKE_INSTALL_MANIFEST_CONTENT
       "${CMAKE_INSTALL_MANIFEST_FILES}")
if(CMAKE_INSTALL_LOCAL_ONLY)
  file(WRITE "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/modules/picolibc/newlib/libc/include/install_local_manifest.txt"
     "${CMAKE_INSTALL_MANIFEST_CONTENT}")
endif()
