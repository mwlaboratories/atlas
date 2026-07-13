#ifndef _KERNEL_VERSION_H_
#define _KERNEL_VERSION_H_

/* The template values come from cmake/version.cmake
 * BUILD_VERSION related template values will be 'git describe',
 * alternatively user defined BUILD_VERSION.
 */

#define ZEPHYR_VERSION_CODE 262400
#define ZEPHYR_VERSION(a,b,c) (((a) << 16) + ((b) << 8) + (c))

#define KERNELVERSION                   0x4010000
#define KERNEL_VERSION_NUMBER           0x40100
#define KERNEL_VERSION_MAJOR            4
#define KERNEL_VERSION_MINOR            1
#define KERNEL_PATCHLEVEL               0
#define KERNEL_TWEAK                    0
#define KERNEL_VERSION_STRING           "4.1.0"
#define KERNEL_VERSION_EXTENDED_STRING  "4.1.0+0"
#define KERNEL_VERSION_TWEAK_STRING     "4.1.0+0"

#define BUILD_VERSION 9df4b12b5af3


#endif /* _KERNEL_VERSION_H_ */
