#!/usr/bin/env python3
"""
Build script for Wabbajack Viewer
Creates executable using PyInstaller with optimized settings
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def clean_build():
    """Clean previous build artifacts"""
    dirs_to_clean = ['build', 'dist', '__pycache__']
    files_to_clean = ['*.spec']
    
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"Cleaning {dir_name}...")
            shutil.rmtree(dir_name)
    
    # Clean .spec files
    for spec_file in Path('.').glob('*.spec'):
        print(f"Removing {spec_file}...")
        spec_file.unlink()

def build_executable():
    """Build the executable using PyInstaller"""
    print("Building Wabbajack Viewer executable...")
    
    # PyInstaller command with optimized settings
    cmd = [
        'pyinstaller',
        '--onefile',                    # Create a single executable file
        '--windowed',                   # Hide console window (GUI app)
        '--name=WabbajackViewer',       # Name of the executable
        '--clean',                      # Clean cache and remove temp files
        '--noconfirm',                  # Replace output directory without asking
        '--add-data=README.md;.',       # Include README if it exists
        'wabbajack_viewer.py'           # Main script
    ]
    
    # Add icon if it exists
    if os.path.exists('icon.ico'):
        cmd.extend(['--icon=icon.ico'])
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("Build successful!")
        print(f"Executable created in: {os.path.abspath('dist')}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        print(f"Error output: {e.stderr}")
        return False

def main():
    """Main build function"""
    print("Wabbajack Viewer Build Script")
    print("=" * 40)
    
    # Check if PyInstaller is installed
    try:
        import PyInstaller
        print(f"PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("Error: PyInstaller not found. Install it with: pip install pyinstaller")
        sys.exit(1)
    
    # Clean previous builds
    clean_build()
    
    # Build executable
    if build_executable():
        print("\nBuild completed successfully!")
        print("You can find the executable in the 'dist' folder.")
    else:
        print("\nBuild failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
