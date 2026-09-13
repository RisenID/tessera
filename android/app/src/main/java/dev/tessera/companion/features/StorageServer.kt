package dev.tessera.companion.features

import android.content.Context
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.util.Log
import dev.tessera.companion.Store
import org.apache.sshd.common.config.keys.KeyUtils
import org.apache.sshd.common.config.keys.PublicKeyEntry
import org.apache.sshd.common.file.nativefs.NativeFileSystemFactory
import org.apache.sshd.common.util.io.PathUtils
import org.apache.sshd.server.SshServer
import org.apache.sshd.server.auth.password.PasswordAuthenticator
import org.apache.sshd.server.keyprovider.SimpleGeneratorHostKeyProvider
import org.apache.sshd.sftp.server.FileHandle
import org.apache.sshd.sftp.server.SftpFileSystemAccessor
import org.apache.sshd.sftp.server.SftpSubsystemFactory
import org.apache.sshd.sftp.server.SftpSubsystemProxy
import java.io.File
import java.io.IOException
import java.nio.channels.Channel
import java.nio.file.CopyOption
import java.nio.file.OpenOption
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest

/** The phone's storage, served over SFTP for the desktop to mount. */
object StorageServer {

    private const val TAG = "TesseraStorage"
    const val USER = "tessera"

    /** Beside the companion link's own 8765, and clear of Sefirah's 5151-5169. */
    private val PORTS = 8766..8775

    data class Info(val port: Int, val password: String, val hostKey: String, val path: String)

    private var server: SshServer? = null
    private var info: Info? = null
    private var users = 0

    /** All files access arrived in Android 11. */
    fun supported(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R

    fun allowed(): Boolean = supported() && Environment.isExternalStorageManager()

    fun grantable(): Boolean = supported() && PrivilegedShell.hasPermission()

    /** Grant All files access through Shizuku. Null when it worked. */
    fun grant(context: Context): String? {
        if (!supported()) return "This phone is older than Android 11."
        if (allowed()) return null
        if (!PrivilegedShell.hasPermission()) {
            return "Shizuku is not running on the phone, so this has to be allowed " +
                "in the phone's settings instead."
        }
        val result = PrivilegedShell.run(
            "appops set --user 0 ${context.packageName} MANAGE_EXTERNAL_STORAGE allow"
        )
        // The app op takes effect at once, but only the platform can say so.
        return if (allowed()) null else result.text.ifBlank { "The phone refused." }
    }

    @Synchronized
    fun start(context: Context): Info {
        info?.let {
            users++
            return it
        }
        if (!allowed()) throw IOException("All files access has not been allowed on the phone.")
        prepare(context)

        val keys = SimpleGeneratorHostKeyProvider(
            File(context.filesDir, "ssh_host_ecdsa").toPath()
        ).apply {
            algorithm = KeyUtils.EC_ALGORITHM
            keySize = 256
        }
        val hostKey = PublicKeyEntry.toString(keys.loadKeys(null).first().public)
        val password = Store.randomHex(16)
        val root = Environment.getExternalStorageDirectory().path

        var last: Exception? = null
        for (port in PORTS) {
            val sshd = SshServer.setUpDefaultServer().apply {
                this.port = port
                keyPairProvider = keys
                fileSystemFactory = NativeFileSystemFactory()
                passwordAuthenticator = PasswordAuthenticator { user, given, _ ->
                    user == USER && MessageDigest.isEqual(
                        given.toByteArray(), password.toByteArray()
                    )
                }
                subsystemFactories = listOf(
                    SftpSubsystemFactory.Builder()
                        .withFileSystemAccessor(MediaIndexing(context.applicationContext, root))
                        .build()
                )
            }
            try {
                sshd.start()
            } catch (e: Exception) {
                last = e
                runCatching { sshd.stop(true) }
                continue
            }
            server = sshd
            users = 1
            Log.i(TAG, "file server on port $port")
            return Info(port, password, hostKey, root).also { info = it }
        }
        throw IOException(last?.message ?: "no free port for the file server", last)
    }

    @Synchronized
    fun release() {
        if (users > 0) users--
        if (users == 0) stop()
    }

    @Synchronized
    fun stop() {
        runCatching { server?.stop(true) }
        server = null
        info = null
        users = 0
    }

    /** What MINA SSHD assumes about a JVM that Android is not. */
    private fun prepare(context: Context) {
        System.setProperty("user.home", context.filesDir.path)
        System.setProperty("org.apache.sshd.security.provider.BC.enabled", "false")
        runCatching { PathUtils.setUserHomeFolderResolver { context.filesDir.toPath() } }
    }

    /** Tell the phone's gallery and music apps about files the desktop changes. */
    private class MediaIndexing(
        private val context: Context,
        private val root: String,
    ) : SftpFileSystemAccessor {

        private fun rescan(path: Path?) {
            val target = path?.toString() ?: return
            if (!target.startsWith(root)) return
            runCatching { MediaScannerConnection.scanFile(context, arrayOf(target), null, null) }
        }

        override fun closeFile(
            subsystem: SftpSubsystemProxy?,
            fileHandle: FileHandle?,
            file: Path?,
            handle: String?,
            channel: Channel?,
            options: MutableSet<out OpenOption>?,
        ) {
            super.closeFile(subsystem, fileHandle, file, handle, channel, options)
            if (options?.contains(StandardOpenOption.WRITE) == true) rescan(file)
        }

        override fun removeFile(subsystem: SftpSubsystemProxy?, path: Path?, isDirectory: Boolean) {
            super.removeFile(subsystem, path, isDirectory)
            rescan(path)
        }

        override fun renameFile(
            subsystem: SftpSubsystemProxy?,
            oldPath: Path?,
            newPath: Path?,
            opts: MutableCollection<CopyOption>?,
        ) {
            super.renameFile(subsystem, oldPath, newPath, opts)
            rescan(oldPath)
            rescan(newPath)
        }
    }
}
