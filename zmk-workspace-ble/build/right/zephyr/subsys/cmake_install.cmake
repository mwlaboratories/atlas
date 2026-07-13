# Install script for directory: /home/gomar/Documents/repos/atlas/zmk-workspace-ble/zephyr/subsys

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
  # Include the install script for each subdirectory.
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/canbus/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/debug/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/fb/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/fs/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/ipc/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/logging/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/mem_mgmt/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/mgmt/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/modbus/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/pm/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/portability/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/random/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/rtio/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/sd/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/stats/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/storage/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/task_wdt/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/testsuite/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/tracing/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/usb/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/bluetooth/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/input/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/retention/cmake_install.cmake")
  include("/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/settings/cmake_install.cmake")

endif()

string(REPLACE ";" "\n" CMAKE_INSTALL_MANIFEST_CONTENT
       "${CMAKE_INSTALL_MANIFEST_FILES}")
if(CMAKE_INSTALL_LOCAL_ONLY)
  file(WRITE "/home/gomar/Documents/repos/atlas/zmk-workspace-ble/build/right/zephyr/subsys/install_local_manifest.txt"
     "${CMAKE_INSTALL_MANIFEST_CONTENT}")
endif()
