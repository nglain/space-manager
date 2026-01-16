# Space Manager for macOS

A visual desktop/spaces manager for macOS with a matrix layout, window tracking, and global hotkeys.

![Space Manager Screenshot](screenshot.png)

## Features

- **Matrix Layout** - View all your desktops in a 2x2, 3x3, 4x4, or 5x5 grid
- **Auto-Detection** - Automatically detects the number of Spaces and adjusts the grid
- **Window Tracking** - Shows which applications and windows are on each desktop
- **Scan All Spaces** - Quickly scan all desktops to get real-time window information
- **Global Hotkey** - `Ctrl+Option+Space` to show/hide the manager from anywhere
- **Quick Switch** - Click on any desktop card to switch to it instantly
- **Custom Names** - Double-click to rename desktops (e.g., "Dev", "Design", "Research")
- **Dark Theme** - Native macOS dark appearance
- **Persistent State** - Remembers window positions and custom names between sessions

## Requirements

- macOS 12+ (Monterey or later)
- Python 3.10+
- PyQt6
- pynput
- pyobjc-framework-Quartz

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/space-manager.git
cd space-manager
```

### 2. Install dependencies

```bash
pip install PyQt6 pynput pyobjc-framework-Quartz
```

Or using the requirements file:

```bash
pip install -r requirements.txt
```

### 3. Grant permissions

The app requires two macOS permissions:

**Accessibility Access** (for global hotkey):
1. Open **System Settings** > **Privacy & Security** > **Accessibility**
2. Add **Terminal** (or your Python interpreter) to the list
3. Enable the checkbox

**Screen Recording** (for window detection - optional but recommended):
1. Open **System Settings** > **Privacy & Security** > **Screen Recording**
2. Add **Terminal** if prompted

### 4. Enable keyboard shortcuts for Spaces

For quick switching to work, enable Mission Control shortcuts:
1. Open **System Settings** > **Keyboard** > **Keyboard Shortcuts** > **Mission Control**
2. Enable **Switch to Desktop 1-9** shortcuts (Ctrl+1, Ctrl+2, etc.)

## Usage

### Run the app

```bash
# Set Qt plugin path if using Anaconda
export QT_PLUGIN_PATH="/opt/anaconda3/lib/python3.12/site-packages/PyQt6/Qt6/plugins"

# Run
python space_manager_v2.py
```

Or use the provided run script:

```bash
./run.sh
```

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Option+Space` | Show/hide Space Manager (global) |
| `1-9` | Switch to desktop 1-9 (when window is focused) |
| `Esc` | Hide the window |
| `Ctrl+Q` | Quit the app |

### Mouse Actions

| Action | Result |
|--------|--------|
| Click on card | Switch to that desktop |
| Double-click on card | Rename the desktop |
| Drag title bar | Move the window |

### Buttons

- **Refresh** - Update windows on current desktop
- **Scan All** - Scan all desktops for windows (will briefly flash through them)
- **Settings** - Change grid size and number of spaces

## Configuration

Settings are stored in `config.json`:

```json
{
  "rows": 3,
  "cols": 3,
  "total_spaces": 8,
  "space_names": {
    "1": "Main",
    "2": "Dev",
    "3": "Research"
  },
  "active_space": 1,
  "space_windows": {}
}
```

## How It Works

### Window Detection

Space Manager uses the Quartz `CGWindowListCopyWindowInfo` API to get the list of windows on the current Space. This is fast and reliable, but macOS only provides information about windows on the **current** desktop.

To get windows from all desktops, use the **"Scan All"** button which:
1. Hides the Space Manager window
2. Quickly switches through each desktop (Ctrl+1, Ctrl+2, etc.)
3. Captures the window list on each
4. Returns to your original desktop
5. Shows the updated Space Manager

### Space Detection

The app reads `~/Library/Preferences/com.apple.spaces.plist` to determine how many Spaces you have configured, then automatically selects the optimal grid size.

## Limitations

- **macOS only** - Uses macOS-specific APIs
- **Max 9 spaces for quick switch** - Ctrl+1-9 keyboard shortcuts only support 9 desktops
- **Window detection requires scanning** - Can't see windows on other Spaces without briefly switching to them
- **Requires Accessibility permission** - For global hotkey functionality

## Troubleshooting

### Hotkey doesn't work
- Make sure Terminal/Python is added to Accessibility in System Settings

### Windows not showing
- Click "Scan All" to refresh all desktops
- Grant Screen Recording permission if prompted

### Qt plugin error
- Set the `QT_PLUGIN_PATH` environment variable for your Python installation

### App crashes on start
- Check `app.log` for error messages
- Ensure all dependencies are installed

## License

MIT License - feel free to use and modify.

## Credits

Created by Claire (AI) for Larry.

---

*If you find this useful, consider giving it a star!*
