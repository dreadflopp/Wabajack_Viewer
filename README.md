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

## Requirements

- Python 3.6+ (for running from source)
- tkinter (included with most Python installations)


