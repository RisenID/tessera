/*
 * The phone in the system tray. Everything comes through `tessera status --json`
 * and the other tessera commands, so the applet needs nothing but the app.
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PC3
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property var status: ({})
    property bool running: false
    readonly property bool connected: running && status.connected === true
    readonly property string statusCommand: "tessera status --json"

    // A shell for the tessera command; each source is run once and dropped.
    P5Support.DataSource {
        id: shell
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            if (source === root.statusCommand) {
                try {
                    root.status = JSON.parse(data.stdout)
                    root.running = true
                } catch (e) {
                    root.status = ({})
                    root.running = false
                }
            }
            disconnectSource(source)
        }
        function run(command) { connectSource(command) }
    }

    function quoted(text) { return "'" + String(text).replace(/'/g, "'\\''") + "'" }
    function tessera(args) { shell.run("tessera " + args) }

    Timer {
        interval: 10000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: shell.run(root.statusCommand)
    }

    readonly property string battery: {
        const b = status.battery || {}
        if (typeof b.level !== "number" || b.level < 0) return ""
        return b.level + "%" + (b.charging ? " charging" : "")
    }
    readonly property string signal: {
        const w = status.wifi || {}, c = status.cell || {}
        const parts = []
        if (w.connected && typeof w.level === "number") parts.push("Wi-Fi " + w.level + "/" + (w.max || 4))
        if (typeof c.level === "number") parts.push((c.type || "cell") + " " + c.level + "/" + (c.max || 4))
        return parts.join(" · ")
    }
    readonly property string summary: {
        if (!running) return "Tessera is not running"
        if (!connected) return "No phone connected"
        return [battery, signal, status.ringer].filter(s => s).join(" · ")
    }

    Plasmoid.icon: connected ? "smartphone" : "smartphone-symbolic"
    Plasmoid.status: connected ? PlasmaCore.Types.ActiveStatus : PlasmaCore.Types.PassiveStatus
    toolTipMainText: running && status.phone ? status.phone : "Tessera"
    toolTipSubText: summary

    compactRepresentation: MouseArea {
        id: compact
        Layout.minimumWidth: Kirigami.Units.iconSizes.small
        Layout.minimumHeight: Kirigami.Units.iconSizes.small
        onClicked: root.expanded = !root.expanded
        Kirigami.Icon {
            anchors.fill: parent
            source: Plasmoid.icon
            active: compact.containsMouse
        }
        PC3.Label {
            visible: root.connected && root.battery !== ""
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            text: (root.status.battery || {}).level
            font.pointSize: Kirigami.Theme.smallFont.pointSize * 0.8
            font.bold: true
        }
        DropArea {
            anchors.fill: parent
            onDropped: drop => root.sendFiles(drop.urls)
        }
    }

    function sendFiles(urls) {
        const paths = []
        for (const url of urls) {
            const text = String(url)
            if (text.startsWith("file://")) paths.push(quoted(decodeURIComponent(text.substring(7))))
        }
        if (paths.length) tessera("send " + paths.join(" "))
    }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.preferredWidth: Kirigami.Units.gridUnit * 20
        spacing: Kirigami.Units.smallSpacing

        DropArea {
            Layout.fillWidth: true
            Layout.fillHeight: true
            onDropped: drop => root.sendFiles(drop.urls)

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Kirigami.Units.largeSpacing
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Heading {
                    level: 3
                    text: root.running && root.status.phone ? root.status.phone : "Tessera"
                }
                PC3.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: root.summary
                }
                PC3.Label {
                    Layout.fillWidth: true
                    visible: root.connected && (root.status.media || {}).title
                    wrapMode: Text.WordWrap
                    opacity: 0.8
                    text: {
                        const m = root.status.media || {}
                        return m.title ? "♪ " + m.title + (m.artist ? " — " + m.artist : "") : ""
                    }
                }
                RowLayout {
                    visible: root.connected && !!root.status.code
                    spacing: Kirigami.Units.smallSpacing
                    PC3.Label {
                        text: "Code " + (root.status.code || "") + (root.status.code_from ? " from " + root.status.code_from : "")
                        font.bold: true
                    }
                    PC3.Button {
                        text: "Copy"
                        onClicked: root.tessera("copy " + root.quoted(root.status.code))
                    }
                }
                PC3.Label {
                    visible: root.connected && !!root.status.presence
                    opacity: 0.8
                    text: "Phone is " + root.status.presence
                }
                Item { Layout.fillHeight: true }
                PC3.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    opacity: 0.7
                    text: root.connected ? "Drop files here to send them to the phone." : "Start Tessera and connect the phone."
                }
                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    PC3.Button {
                        text: "Ring"
                        icon.name: "tessera-ring-phone"
                        enabled: root.connected
                        onClicked: root.tessera("ring")
                    }
                    PC3.Button {
                        text: "Photo"
                        icon.name: "camera-photo"
                        enabled: root.connected
                        onClicked: root.tessera("photo")
                    }
                    PC3.Button {
                        text: "Open link"
                        icon.name: "link"
                        enabled: root.connected
                        onClicked: root.tessera("open")
                    }
                    PC3.Button {
                        text: "Show"
                        icon.name: "window"
                        onClicked: root.tessera("show")
                    }
                }
            }
        }
    }
}
