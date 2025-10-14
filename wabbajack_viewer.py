import json
import os
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from datetime import datetime
import webbrowser
import zipfile
import tempfile

class WabbajackGuideApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Wabbajack Manual Installation Guide")
        self.root.geometry("1200x800")
        
        # Make window resizable
        self.root.resizable(True, True)
        
        # Initialize data
        self.modlist_data = None
        self.archives = []
        self.directives = []
        self.archive_lookup = {}
        self.mod_details = {}
        self.current_wabbajack_file = None
        
        self.setup_ui()
        self.show_load_prompt()
        
    def show_load_prompt(self):
        """Show dialog to load Wabbajack file"""
        result = messagebox.askyesno(
            "Load Wabbajack File", 
            "Would you like to load a Wabbajack file?\n\n"
            "This will extract the modlist.json from the Wabbajack archive."
        )
        if result:
            self.load_wabbajack_file()
    
    def load_wabbajack_file(self):
        """Open file dialog to select and load Wabbajack file"""
        file_path = filedialog.askopenfilename(
            title="Select Wabbajack File",
            filetypes=[
                ("Wabbajack files", "*.wabbajack"),
                ("All files", "*.*")
            ]
        )
        
        if file_path:
            self.current_wabbajack_file = file_path
            self.extract_and_load_modlist(file_path)
    
    def extract_and_load_modlist(self, wabbajack_path):
        """Extract modlist.json from Wabbajack file and load it"""
        try:
            # Create temporary directory for extraction
            with tempfile.TemporaryDirectory() as temp_dir:
                # Extract modlist.json from Wabbajack file
                with zipfile.ZipFile(wabbajack_path, 'r') as zip_ref:
                    # Look for modlist file (could be named 'modlist' or 'modlist.json')
                    modlist_files = [f for f in zip_ref.namelist() if f == 'modlist' or f == 'modlist.json']
                    
                    if not modlist_files:
                        messagebox.showerror("Error", "No modlist file found in Wabbajack archive")
                        return
                    
                    modlist_file = modlist_files[0]
                    zip_ref.extract(modlist_file, temp_dir)
                    
                    # Load and parse the modlist JSON
                    modlist_path = os.path.join(temp_dir, modlist_file)
                    with open(modlist_path, 'r', encoding='utf-8') as f:
                        self.modlist_data = json.load(f)
                
                # Process the loaded data
                self.archives = self.modlist_data.get('Archives', [])
                self.directives = self.modlist_data.get('Directives', [])
                
                # Create archive lookup
                for archive in self.archives:
                    archive_hash = archive.get('Hash', '')
                    archive_name = archive.get('Name', '')
                    self.archive_lookup[archive_hash] = archive_name
                
                # Process directives for mod details
                self.process_directives()
                
                # Update UI
                self.update_ui_after_load()
                
                messagebox.showinfo("Success", f"Successfully loaded modlist from:\n{os.path.basename(wabbajack_path)}")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load Wabbajack file: {e}")
    
    def update_ui_after_load(self):
        """Update UI elements after loading modlist data"""
        # Update header
        if self.modlist_data:
            title = f"Manual Installation Guide: {self.modlist_data.get('Name', 'Unknown Modlist')}"
            self.root.title(f"Wabbajack Manual Installation Guide - {self.modlist_data.get('Name', 'Unknown Modlist')}")
        
        # Update modlist info
        if hasattr(self, 'info_label'):
            if self.modlist_data:
                info_text = f"""
Name: {self.modlist_data.get('Name', 'Unknown')}
Author: {self.modlist_data.get('Author', 'Unknown')}
Version: {self.modlist_data.get('Version', 'Unknown')}
Game: {self.modlist_data.get('GameType', 'Unknown')}
Description: {self.modlist_data.get('Description', 'No description available')}
Wabbajack Version: {self.modlist_data.get('WabbajackVersion', 'Unknown')}
Total Mods: {len(self.archives)}
                """
                self.info_label.config(text=info_text)
        
        # Populate mod list
        self.populate_mod_list()
        
        # Update status
        if hasattr(self, 'status_var'):
            self.status_var.set("No modlist loaded")
    
    def load_modlist_data(self):
        """Load and parse the modlist JSON file (kept for compatibility)"""
        # This method is now deprecated, use load_wabbajack_file instead
        pass
    
    def process_directives(self):
        """Process directives to create mod details lookup"""
        for directive in self.directives:
            if directive.get('$type') == 'FromArchive':
                archive_hash = directive.get('ArchiveHashPath', [''])[0] if directive.get('ArchiveHashPath') else ''
                archive_path = directive.get('ArchiveHashPath', ['', ''])[1] if len(directive.get('ArchiveHashPath', [])) > 1 else ''
                
                if archive_hash not in self.mod_details:
                    self.mod_details[archive_hash] = []
                
                self.mod_details[archive_hash].append({
                    'target_path': directive.get('To', ''),
                    'archive_path': archive_path,
                    'size': directive.get('Size', 0)
                })
    
    def setup_ui(self):
        """Setup the main UI components"""
        # Create main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights for resizable UI
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # Header
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.title_label = ttk.Label(header_frame, text="Wabbajack Manual Installation Guide", font=('Arial', 16, 'bold'))
        self.title_label.pack()
        
        # Load file button
        load_button = ttk.Button(header_frame, text="Load Wabbajack File", command=self.load_wabbajack_file)
        load_button.pack(pady=(5, 0))
        
        # Modlist info
        info_frame = ttk.LabelFrame(main_frame, text="Modlist Information", padding="10")
        info_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.info_label = ttk.Label(info_frame, text="No modlist loaded. Click 'Load Wabbajack File' to begin.", justify=tk.LEFT)
        self.info_label.pack(anchor=tk.W)
        
        # Search frame
        search_frame = ttk.Frame(main_frame)
        search_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        
        ttk.Label(search_frame, text="Search Mods:").pack(anchor=tk.W)
        self.search_var = tk.StringVar()
        self.search_var.trace('w', self.filter_mods)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(anchor=tk.W, pady=(5, 0))
        
        # Create PanedWindow for resizable mod list and details
        paned_window = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned_window.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Mod list frame
        mod_frame = ttk.LabelFrame(paned_window, text="Mod List", padding="10")
        mod_frame.columnconfigure(0, weight=1)
        mod_frame.rowconfigure(0, weight=1)
        
        # Create treeview for mods
        self.tree = ttk.Treeview(mod_frame, columns=('author', 'version', 'size'), show='tree headings')
        self.tree.heading('#0', text='Mod Name')
        self.tree.heading('author', text='Author')
        self.tree.heading('version', text='Version')
        self.tree.heading('size', text='Size (MB)')
        
        # Configure column widths
        self.tree.column('#0', width=400, minwidth=300)
        self.tree.column('author', width=150, minwidth=100)
        self.tree.column('version', width=100, minwidth=80)
        self.tree.column('size', width=100, minwidth=80)
        
        # Scrollbar for treeview
        tree_scroll = ttk.Scrollbar(mod_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # Details frame
        details_frame = ttk.LabelFrame(paned_window, text="Mod Details", padding="10")
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)
        
        # Create notebook for tabs
        self.notebook = ttk.Notebook(details_frame)
        self.notebook.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Overview tab
        overview_frame = ttk.Frame(self.notebook)
        self.notebook.add(overview_frame, text="Overview")
        
        # Text widget for overview with clickable links
        self.overview_text = scrolledtext.ScrolledText(overview_frame, wrap=tk.WORD)
        self.overview_text.pack(fill=tk.BOTH, expand=True)
        
        # Configure text widget for links
        self.overview_text.tag_configure("link", foreground="blue", underline=True)
        self.overview_text.tag_bind("link", "<Button-1>", self.open_link)
        self.overview_text.tag_bind("link", "<Enter>", lambda e: self.overview_text.config(cursor="hand2"))
        self.overview_text.tag_bind("link", "<Leave>", lambda e: self.overview_text.config(cursor=""))
        
        # Files tab
        files_frame = ttk.Frame(self.notebook)
        self.notebook.add(files_frame, text="Files")
        
        # Create treeview for files
        self.files_tree = ttk.Treeview(files_frame, columns=('source_path', 'size'), show='tree headings')
        self.files_tree.heading('#0', text='Target Path')
        self.files_tree.heading('source_path', text='Source Path')
        self.files_tree.heading('size', text='Size (bytes)')
        
        # Configure column widths
        self.files_tree.column('#0', width=300, minwidth=200)
        self.files_tree.column('source_path', width=300, minwidth=200)
        self.files_tree.column('size', width=100, minwidth=80)
        
        # Scrollbar for files treeview
        files_scroll = ttk.Scrollbar(files_frame, orient=tk.VERTICAL, command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=files_scroll.set)
        
        self.files_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        files_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(0, weight=1)
        
        # Add frames to PanedWindow
        paned_window.add(mod_frame, weight=2)  # Mod list gets more initial space
        paned_window.add(details_frame, weight=1)  # Details gets less initial space
        
        # Bind tree selection event
        self.tree.bind('<<TreeviewSelect>>', self.on_mod_select)
        
        # Populate mod list (will be empty initially)
        self.populate_mod_list()
        
        # Status bar
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))
        
        self.status_var = tk.StringVar()
        self.status_var.set("No modlist loaded")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT)
        
        # Generated timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ttk.Label(status_frame, text=f"Generated: {timestamp}").pack(side=tk.RIGHT)
    
    def open_link(self, event):
        """Open link in default browser"""
        try:
            # Get the clicked position
            index = self.overview_text.index(f"@{event.x},{event.y}")
            # Get the line containing the click
            line_start = self.overview_text.index(f"{index} linestart")
            line_end = self.overview_text.index(f"{index} lineend")
            line_text = self.overview_text.get(line_start, line_end)
            
            # Extract URL from the line
            if "Mod Page:" in line_text:
                url = line_text.split("Mod Page:")[1].strip()
                webbrowser.open(url)
            elif "Direct Download:" in line_text:
                url = line_text.split("Direct Download:")[1].strip()
                webbrowser.open(url)
            elif "Download:" in line_text and "Direct download (link not available)" not in line_text:
                url = line_text.split("Download:")[1].strip()
                webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open link: {e}")
    
    def organize_files_by_directory(self, file_list):
        """Organize files into a directory tree structure"""
        tree = {}
        
        for file_info in file_list:
            target_path = file_info['target_path']
            archive_path = file_info['archive_path']
            size = file_info['size']
            
            # Split target path into parts
            parts = target_path.split('/')
            
            # Build tree structure
            current = tree
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    current[part] = {'type': 'dir', 'children': {}}
                current = current[part]['children']
            
            # Add file
            filename = parts[-1]
            current[filename] = {
                'type': 'file', 
                'size': size, 
                'target_path': target_path,
                'archive_path': archive_path
            }
        
        return tree
    
    def populate_files_tree(self, file_tree, parent=""):
        """Recursively populate the files treeview"""
        for name, item in file_tree.items():
            if item['type'] == 'dir':
                # Create directory node
                dir_id = self.files_tree.insert(parent, 'end', text=f"📁 {name}/", 
                                              values=("", ""), tags=('directory',))
                # Recursively add children
                self.populate_files_tree(item['children'], dir_id)
            else:
                # Create file node
                size_str = f"{item['size']:,}" if item['size'] > 0 else "0"
                source_path = item.get('archive_path', 'Unknown')
                self.files_tree.insert(parent, 'end', text=f"📄 {name}", 
                                     values=(source_path, size_str), tags=('file',))
    
    def populate_mod_list(self):
        """Populate the treeview with mod data"""
        self.tree.delete(*self.tree.get_children())
        
        for i, archive in enumerate(self.archives):
            state = archive.get('State', {})
            filename = archive.get('Name', 'Unknown Mod')
            size = archive.get('Size', 0)
            archive_hash = archive.get('Hash', '')
            
            # Get mod details
            mod_author = state.get('Author', 'Unknown')
            mod_version = state.get('Version', '')
            actual_mod_name = state.get('Name', filename)
            
            # Calculate size in MB
            size_mb = size / (1024 * 1024) if size > 0 else 0
            
            # Insert into treeview with archive_hash as a tag
            item_id = self.tree.insert('', 'end', 
                                     text=f"{i+1}. {actual_mod_name}",
                                     values=(mod_author, mod_version, f"{size_mb:.1f}"),
                                     tags=(archive_hash,))
    
    def filter_mods(self, *args):
        """Filter mods based on search text"""
        search_text = self.search_var.get().lower()
        
        # Hide all items first
        for item in self.tree.get_children():
            self.tree.detach(item)
        
        # Show only matching items
        for i, archive in enumerate(self.archives):
            state = archive.get('State', {})
            filename = archive.get('Name', 'Unknown Mod')
            size = archive.get('Size', 0)
            archive_hash = archive.get('Hash', '')
            
            mod_author = state.get('Author', 'Unknown')
            mod_version = state.get('Version', '')
            actual_mod_name = state.get('Name', filename)
            
            # Check if search text matches
            if (search_text in actual_mod_name.lower() or 
                search_text in mod_author.lower() or 
                search_text in filename.lower()):
                
                size_mb = size / (1024 * 1024) if size > 0 else 0
                
                item_id = self.tree.insert('', 'end',
                                         text=f"{i+1}. {actual_mod_name}",
                                         values=(mod_author, mod_version, f"{size_mb:.1f}"),
                                         tags=(archive_hash,))
        
        # Update status
        visible_count = len(self.tree.get_children())
        self.status_var.set(f"Showing {visible_count} of {len(self.archives)} mods")
    
    def on_mod_select(self, event):
        """Handle mod selection and show details"""
        selection = self.tree.selection()
        if not selection:
            return
        
        item = selection[0]
        # Get archive_hash from the item's tags
        item_tags = self.tree.item(item, 'tags')
        archive_hash = item_tags[0] if item_tags else ""
        
        # Find the archive data
        archive = None
        for arch in self.archives:
            if arch.get('Hash') == archive_hash:
                archive = arch
                break
        
        if not archive:
            return
        
        # Get mod details
        state = archive.get('State', {})
        filename = archive.get('Name', 'Unknown Mod')
        size = archive.get('Size', 0)
        
        mod_type = state.get('$type', '')
        mod_page_link = ""
        direct_file_link = ""
        mod_author = state.get('Author', 'Unknown')
        mod_description = state.get('Description', '')
        mod_version = state.get('Version', '')
        actual_mod_name = state.get('Name', filename)
        
        # Generate links
        if 'NexusDownloader' in mod_type:
            mod_id = state.get('ModID')
            file_id = state.get('FileID')
            game_name = state.get('GameName', 'skyrimse')
            
            if mod_id and file_id:
                if game_name.lower() == 'skyrimspecialedition':
                    mod_page_link = f"https://www.nexusmods.com/skyrimspecialedition/mods/{mod_id}"
                    direct_file_link = f"https://www.nexusmods.com/skyrimspecialedition/mods/{mod_id}?tab=files&file_id={file_id}"
                else:
                    mod_page_link = f"https://www.nexusmods.com/{game_name.lower()}/mods/{mod_id}"
                    direct_file_link = f"https://www.nexusmods.com/{game_name.lower()}/mods/{mod_id}?tab=files&file_id={file_id}"
        
        elif 'HttpDownloader' in mod_type:
            direct_file_link = state.get('Url', '')
        
        # Clear previous content
        self.overview_text.delete(1.0, tk.END)
        
        # Build overview text with clickable links
        overview = f"Mod Name: {actual_mod_name}\n"
        overview += f"Filename: {filename}\n"
        overview += f"Author: {mod_author}\n"
        if mod_version:
            overview += f"Version: {mod_version}\n"
        
        size_mb = size / (1024 * 1024) if size > 0 else 0
        overview += f"Archive Size: {size_mb:.1f} MB\n\n"
        
        if mod_description:
            overview += f"Description: {mod_description}\n\n"
        
        # Add download links with clickable formatting
        if mod_page_link and direct_file_link:
            overview += f"Mod Page: {mod_page_link}\n"
            overview += f"Direct Download: {direct_file_link}\n\n"
        elif direct_file_link:
            overview += f"Download: {direct_file_link}\n\n"
        else:
            overview += "Download: Direct download (link not available)\n\n"
        
        # Add file count information
        if archive_hash in self.mod_details:
            file_list = self.mod_details[archive_hash]
            overview += f"Files to be installed: {len(file_list)} files\n"
            total_size = sum(f['size'] for f in file_list)
            overview += f"Total file size: {total_size:,} bytes ({total_size / (1024*1024):.1f} MB)\n"
        else:
            overview += "No file details available for this mod.\n"
        
        # Insert text and make links clickable
        self.overview_text.insert(1.0, overview)
        self.make_links_clickable()
        
        # Update files tab (but don't switch to it)
        self.update_files_tab(archive_hash)
        
        # Switch to overview tab by default
        self.notebook.select(0)
    
    def update_files_tab(self, archive_hash):
        """Update the files tab with organized file tree"""
        # Clear previous files
        self.files_tree.delete(*self.files_tree.get_children())
        
        if archive_hash in self.mod_details:
            file_list = self.mod_details[archive_hash]
            
            # Organize files by directory
            file_tree = self.organize_files_by_directory(file_list)
            
            # Populate the tree
            self.populate_files_tree(file_tree)
        else:
            # Show message if no files
            self.files_tree.insert('', 'end', text="No file details available for this mod.")
    
    def make_links_clickable(self):
        """Make URLs in the text widget clickable"""
        content = self.overview_text.get(1.0, tk.END)
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            if "Mod Page:" in line:
                start = f"{i+1}.{line.find('Mod Page:') + 10}"
                end = f"{i+1}.{len(line)}"
                self.overview_text.tag_add("link", start, end)
            elif "Direct Download:" in line:
                start = f"{i+1}.{line.find('Direct Download:') + 17}"
                end = f"{i+1}.{len(line)}"
                self.overview_text.tag_add("link", start, end)
            elif "Download:" in line and "Direct download (link not available)" not in line:
                start = f"{i+1}.{line.find('Download:') + 10}"
                end = f"{i+1}.{len(line)}"
                self.overview_text.tag_add("link", start, end)

def main():
    root = tk.Tk()
    app = WabbajackGuideApp(root)
    root.mainloop()

if __name__ == "__main__":
    main() 