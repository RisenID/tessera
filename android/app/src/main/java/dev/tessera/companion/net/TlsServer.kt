package dev.tessera.companion.net

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import java.math.BigInteger
import java.net.InetSocketAddress
import java.net.Socket
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.Calendar
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.security.auth.x500.X500Principal

/**
 * TLS listener whose identity is a self-signed certificate held in AndroidKeyStore.
 *
 * The desktop pins this certificate's SHA-256 fingerprint when it pairs, so the
 * certificate *is* the phone's long-term identity and is never rotated --
 * rotating it would invalidate every pairing.
 *
 * AndroidKeyStore generates the self-signed certificate itself, which avoids
 * both shipping a certificate library and reflecting into the platform's
 * hidden Bouncy Castle copy (blocked from Android 12 onwards). The private key
 * is hardware-backed where the device supports it and cannot be extracted.
 */
class TlsServer {

    private var serverSocket: SSLServerSocket? = null

    val localPort: Int
        get() = serverSocket?.localPort ?: -1

    fun start(port: Int): SSLServerSocket {
        val keyStore = androidKeyStore()
        ensureIdentity(keyStore)

        // A null password is correct here: AndroidKeyStore keys are guarded by
        // the keystore itself, not by a passphrase we would have to store.
        val keyManagers = KeyManagerFactory
            .getInstance(KeyManagerFactory.getDefaultAlgorithm())
            .apply { init(keyStore, null) }
            .keyManagers

        val sslContext = SSLContext.getInstance("TLS").apply {
            init(keyManagers, null, null)
        }

        val socket = (sslContext.serverSocketFactory.createServerSocket() as SSLServerSocket).apply {
            reuseAddress = true
            // Every interface: the desktop may arrive over Wi-Fi or over a
            // USB tether, and the service should not care which.
            bind(InetSocketAddress(port))
        }
        serverSocket = socket
        Log.i(TAG, "listening on ${socket.localPort}")
        return socket
    }

    fun stop() {
        try {
            serverSocket?.close()
        } catch (e: Exception) {
            Log.d(TAG, "closing listener: ${e.message}")
        }
        serverSocket = null
    }

    fun accept(): Socket? = try {
        serverSocket?.accept()
    } catch (e: Exception) {
        // A close() from another thread lands here; only note it if we still
        // believed we were listening.
        if (serverSocket != null) Log.d(TAG, "accept failed: ${e.message}")
        null
    }

    /** SHA-256 of the server certificate, hex, shown beside the pairing code. */
    fun certificateFingerprint(): String {
        val keyStore = androidKeyStore()
        ensureIdentity(keyStore)
        val certificate = keyStore.getCertificate(ALIAS) as? X509Certificate
            ?: return ""
        return MessageDigest.getInstance("SHA-256")
            .digest(certificate.encoded)
            .joinToString("") { "%02x".format(it) }
    }

    private fun androidKeyStore(): KeyStore =
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun ensureIdentity(keyStore: KeyStore) {
        // Identities generated before the decrypt purpose was added cannot
        // complete an RSA-key-exchange handshake, so they are replaced rather
        // than reused. This changes the fingerprint, which means re-pairing.
        if (keyStore.containsAlias(LEGACY_ALIAS)) {
            runCatching { keyStore.deleteEntry(LEGACY_ALIAS) }
            Log.i(TAG, "removed the old TLS identity; desktops must pair again")
        }
        if (keyStore.containsAlias(ALIAS)) return

        val notBefore = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -1) }
        val notAfter = Calendar.getInstance().apply { add(Calendar.YEAR, 20) }

        val spec = KeyGenParameterSpec.Builder(
            ALIAS,
            // Signing alone is not enough. An ECDHE handshake only needs the
            // server to sign, but the client may negotiate an RSA key-exchange
            // suite instead, where the server decrypts the premaster secret
            // with RSA/ECB/NoPadding. Without DECRYPT and the matching
            // encryption paddings, Keystore rejects that with
            // INCOMPATIBLE_PADDING_MODE and the handshake dies with an opaque
            // "Failure in SSL library".
            KeyProperties.PURPOSE_SIGN or
                KeyProperties.PURPOSE_VERIFY or
                KeyProperties.PURPOSE_DECRYPT or
                KeyProperties.PURPOSE_ENCRYPT,
        )
            .setKeySize(2048)
            .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA512, KeyProperties.DIGEST_NONE)
            .setSignaturePaddings(
                KeyProperties.SIGNATURE_PADDING_RSA_PKCS1,
                KeyProperties.SIGNATURE_PADDING_RSA_PSS,
            )
            .setEncryptionPaddings(
                KeyProperties.ENCRYPTION_PADDING_NONE,
                KeyProperties.ENCRYPTION_PADDING_RSA_PKCS1,
            )
            .setRandomizedEncryptionRequired(false)
            .setCertificateSubject(X500Principal("CN=Tessera Companion, O=Tessera"))
            .setCertificateSerialNumber(BigInteger.valueOf(System.currentTimeMillis()))
            .setCertificateNotBefore(notBefore.time)
            .setCertificateNotAfter(notAfter.time)
            .build()

        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_RSA, "AndroidKeyStore")
            .apply { initialize(spec) }
            .generateKeyPair()

        Log.i(TAG, "generated a new TLS identity")
    }

    companion object {
        private const val TAG = "TesseraTls"
        private const val ALIAS = "tessera-identity-v2"
        private const val LEGACY_ALIAS = "tessera-identity"
    }
}
