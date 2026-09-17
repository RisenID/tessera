# The desktop runs this class by name through app_process over adb, so nothing
# inside the app refers to it and R8 would otherwise drop it.
-keep class dev.risenid.tessera.shell.ClipboardHelper {
    public static void main(java.lang.String[]);
    *;
}

# Apache MINA SSHD (the phone's SFTP server) picks factories and subsystems by
# name at run time; shrinking it breaks the storage mount rather than the build.
-keep class org.apache.sshd.** { *; }
-dontwarn org.apache.sshd.**
-dontwarn org.slf4j.**
-dontwarn org.bouncycastle.**
-dontwarn org.apache.tomcat.**
-dontwarn javax.naming.**
-dontwarn java.awt.**

# Shizuku's binder plumbing, and the bypass that reaches non-SDK APIs.
-keep class rikka.shizuku.** { *; }
-dontwarn rikka.shizuku.**
-keep class org.lsposed.hiddenapibypass.** { *; }

# Views named in layouts, and the two-argument constructor the inflater calls.
-keep class dev.risenid.tessera.TrackpadView { public <init>(...); }
-keep class dev.risenid.tessera.RemoteEditText { public <init>(...); }

# Line numbers in a Play crash report, without the rest of the file's name.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
