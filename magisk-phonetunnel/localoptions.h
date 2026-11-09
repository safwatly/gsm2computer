#ifndef DROPBEAR_LOCALOPTIONS_H_
#define DROPBEAR_LOCALOPTIONS_H_

/* Key-only. Password authentication is compiled out so a runtime flag
 * cannot turn it back on. */
#define DROPBEAR_SVR_PASSWORD_AUTH 0
#define DROPBEAR_CLI_PASSWORD_AUTH 0

/* The phone has no useful /etc/motd, and reading one is wasted work. */
#define DO_MOTD 0

/* See patches/android-root-login.patch. Without a passwd entry,
 * getpwnam("root") fails and the sshd refuses the login. */
#define DROPBEAR_ANDROID_ROOT 1

#endif
