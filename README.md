# Wabbajack Viewer

A graphical tool for viewing and analyzing Wabbajack modlist files (.wabbajack). This application helps users understand what mods are included in a modlist and provides manual installation guidance.

## Features

- **Load Wabbajack Files**: Extract and parse modlist.json from .wabbajack archives
- **Mod Browser**: Searchable list of all mods with details (name, author, version, size)
- **Detailed Information**: Comprehensive mod details including:
  - Mod metadata and descriptions
  - Clickable links to Nexus Mods pages
  - Direct download links
  - File structure and installation paths
- **Interactive Interface**: Resizable GUI with tabbed views and clickable links

## Installation

### Option 1: Download Executable (Recommended)
Download the latest executable from the [Releases](https://github.com/yourusername/Wabajack_Viewer/releases) page.

### Option 2: Run from Source
1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/Wabajack_Viewer.git
   cd Wabajack_Viewer
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python wabbajack_viewer.py
   ```

## Usage

1. Launch the application
2. Click "Load Wabbajack File" and select a .wabbajack file
3. Browse the mod list and use the search function to find specific mods
4. Click on any mod to view detailed information including:
   - Mod page links (clickable)
   - Direct download links (clickable)
   - File structure that will be installed
5. Use the tabs to switch between Overview and Files views

## Building from Source

To build your own executable:

```bash
python build.py
```

This will create a standalone executable in the `dist` folder.

## Requirements

- Python 3.6+ (for running from source)
- tkinter (included with most Python installations)
- PyInstaller (for building executables)

## Supported Platforms

- Windows
- Linux
- macOS

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

This project is open source. Please check the license file for details.

## Troubleshooting

### tkinter not found
If you get a tkinter error:
- **Ubuntu/Debian**: `sudo apt-get install python3-tk`
- **CentOS/RHEL**: `sudo yum install tkinter`
- **macOS**: tkinter should be included with Python
- **Windows**: tkinter is included with Python

### Build Issues
If you encounter build issues:
1. Make sure PyInstaller is installed: `pip install pyinstaller`
2. Try cleaning the build: `python build.py` (includes cleanup)
3. Check that all dependencies are installed: `pip install -r requirements.txt`
