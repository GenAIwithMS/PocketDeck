<div align="center">

# PocketDeck

**Turn your phone into a wireless touchpad and keyboard for your laptop.**

No phone app needed · Wi-Fi or Bluetooth · Windows & Linux

</div>

---

PocketDeck runs on your laptop and serves a touchpad + keyboard page. Open it in your
phone's browser and control the laptop from the phone.

- Runs on **Windows and Linux** (Wayland + X11)
- Python 3.8+, **no extra packages needed**

## Features

### Touchpad

| Gesture on phone          | Action                                   |
|---------------------------|------------------------------------------|
| 1 finger move             | Move pointer (with acceleration)         |
| 1 finger tap              | Left click (tap twice = double click)    |
| Tap, then touch and move  | Drag (hold left button)                  |
| 2 finger move             | Scroll (vertical + horizontal, smooth, with inertia) |
| 2 finger tap              | Right click                              |
| Pinch                     | Zoom (Ctrl + wheel)                      |
| 3 finger tap              | Middle click                             |
| 3 finger swipe ↑ / ↓      | Task view / show desktop                 |
| 3 finger swipe ← / →      | Switch app (Alt+Tab)                     |
| 4 finger swipe ← / →      | Switch virtual desktop                   |
| Left / Middle / Right bar | Physical buttons (hold one + move to drag) |

### Keyboard

Tap **⌨** at the top of the page to open your phone's keyboard. What you type goes to the
laptop, including autocorrect, suggestions and swipe typing. The touchpad keeps working
while the keyboard is open.

A row of extra keys sits above the text field (scroll it sideways):
**Ctrl Alt Shift Win** (tap to hold for the next key, e.g. Ctrl then T = Ctrl+T),
**Esc Tab arrows Home End PgUp PgDn Del**, **Copy Paste Cut Undo Select-all**, **F1–F12**,
volume, play/pause and PrtSc.

Languages and emoji: Windows types every language directly. On Linux, English
letters/symbols are typed as key presses (US layout), and other characters (Urdu, emoji…)
are pasted through the clipboard. That needs `wl-clipboard` on Wayland or `xclip` on X11.

Settings (⚙ on the page): pointer speed, acceleration, scroll speed, tap-to-click,
natural scrolling, inertia, vibration.

## Setup

### Option 1: Wi-Fi (easiest)

1. Connect the phone and the laptop to the **same Wi-Fi network**.
2. Start the program on the laptop:
   ```
   ./run.sh                      # Linux   (or: python3 pocketdeck.py)
   run.bat                       # Windows (or: python pocketdeck.py)
   ```
3. It prints a **PIN** and an address tagged `[Wi-Fi/LAN]`, e.g. `http://192.168.1.20:8765`.
   Open that address in your phone's browser.
4. Enter the PIN once. The phone remembers it.

Tip: use the browser's *Add to Home screen* so you can open it full-screen with one tap.

Some public networks (cafés, hotels, universities) block devices from reaching each
other. If the page won't load there, use Bluetooth instead.

### Option 2: Bluetooth (no Wi-Fi needed)

You use **Bluetooth tethering**, which creates a small network between the phone and
laptop over Bluetooth. Set it up once:

**On the phone**
- Android: pair with the laptop, then *Settings → Network → Hotspot & tethering → Bluetooth tethering → ON*
- iPhone: *Settings → Personal Hotspot → ON* (requires a carrier plan with hotspot; Bluetooth is used automatically after pairing)

**On the laptop**
- **Linux (KDE):** System Settings → Bluetooth → pair the phone → select it → *Connect* "Network Access Point".
  Or from a terminal:
  ```
  nmcli connection add type bluetooth con-name PhonePAN bt-type panu bluetooth.bdaddr <PHONE-MAC> ipv4.never-default yes ipv6.never-default yes
  nmcli connection up PhonePAN
  ```
  (`never-default` keeps your internet on Wi-Fi.)
- **Linux (GNOME):** Settings → Bluetooth → phone → enable "Network/Use your mobile phone as a network device".
- **Windows:** pair the phone → Control Panel → *Devices and Printers* → right-click the phone →
  *Connect using → Access point*.

Then run the program as in Option 1 and open the address tagged `[BLUETOOTH]`,
e.g. `http://192.168.44.23:8765`. If you started the program before connecting
Bluetooth, the new address is printed automatically when the link comes up.

### Troubleshooting

- **Windows:** allow Python when the firewall asks (Private networks).
- **Linux:** if `/dev/uinput` permission is denied, the program prints the one-time fix.

## Options

```
--port 8765      change port
--pin 123456     set your own PIN (remembered)
```

### Custom gestures (`config.json`, optional)

```json
{
  "desktop": "kde",
  "gestures": {
    "swipe3_up": ["super", "w"],
    "swipe4_down": ["super", "d"]
  }
}
```
`desktop` is `windows`, `kde` or `gnome` (detected automatically). Key names:
`ctrl shift alt super tab esc enter space backspace delete up down left right pageup pagedown
home end f1–f12 a–z 0–9 volumeup volumedown mute playpause nextsong previoussong`.

## How it works

```
Phone browser (touch + keyboard) --WebSocket over Wi-Fi or Bluetooth--> pocketdeck.py
   Linux:   /dev/uinput virtual mouse + keyboard
   Windows: SendInput API
```
Gestures are recognised on the phone. The laptop receives small commands
(move / scroll / click / gesture / text) and injects them as real input.
