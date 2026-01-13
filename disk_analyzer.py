"""
DiskAnalyzer - Simple disk usage analyzer with GUI
A minimal application to analyze folder storage usage with tree view and treemap visualization.
"""

import sys
import os
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTreeWidget, QTreeWidgetItem, QFileDialog, QLabel,
    QProgressBar, QSplitter, QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRectF
from PyQt6.QtGui import QColor, QBrush, QPen, QFont, QIcon


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class FolderInfo:
    """Represents a folder with its size information."""
    path: str
    size: int
    children: List['FolderInfo']
    
    @property
    def total_size(self) -> int:
        """Calculate total size including all children."""
        return self.size + sum(child.total_size for child in self.children)


# ============================================================================
# Disk Scanning Logic
# ============================================================================

class DiskScanner:
    """Scans disk folders and calculates sizes."""
    
    def __init__(self):
        self.cancelled = False
    
    def scan(self, root_path: str) -> Optional[FolderInfo]:
        """
        Recursively scan folder and return FolderInfo tree.
        Returns None if cancelled.
        """
        try:
            path = Path(root_path)
            if not path.exists():
                return None
            
            return self._scan_folder(path)
        except Exception as e:
            print(f"Error scanning {root_path}: {e}")
            return None
    
    def _scan_folder(self, path: Path) -> FolderInfo:
        """Recursively scan a folder."""
        if self.cancelled:
            return FolderInfo(str(path), 0, [])
        
        try:
            children = []
            
            # Scan all items in this folder
            for item in path.iterdir():
                if item.is_file():
                    # Add files as leaf nodes
                    try:
                        file_size = item.stat().st_size
                        file_info = FolderInfo(str(item), file_size, [])
                        children.append(file_info)
                    except (PermissionError, OSError):
                        # Skip files we can't access
                        pass
                elif item.is_dir():
                    # Recursively scan subfolders
                    try:
                        child_info = self._scan_folder(item)
                        children.append(child_info)
                    except (PermissionError, OSError):
                        # Skip folders we can't access
                        pass
            
            return FolderInfo(str(path), 0, children)
        except (PermissionError, OSError):
            return FolderInfo(str(path), 0, [])


# ============================================================================
# Worker Thread for Non-Blocking UI
# ============================================================================

class ScannerWorker(QThread):
    """Worker thread for scanning folders."""
    
    progress = pyqtSignal(str)  # Current folder being scanned
    finished = pyqtSignal(FolderInfo)  # Scan complete
    error = pyqtSignal(str)  # Error occurred
    
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.scanner = DiskScanner()
    
    def run(self):
        """Execute scan in background thread."""
        try:
            self.progress.emit(f"Scanning {self.path}...")
            result = self.scanner.scan(self.path)
            if result:
                self.finished.emit(result)
            else:
                self.error.emit("Failed to scan folder")
        except Exception as e:
            self.error.emit(str(e))
    
    def stop(self):
        """Stop scanning."""
        self.scanner.cancelled = True


# ============================================================================
# Utility Functions
# ============================================================================

def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def calculate_percentage(size: int, total: int) -> float:
    """Calculate percentage safely."""
    return (size / total * 100) if total > 0 else 0


# ============================================================================
# Treemap Visualization
# ============================================================================

class TreemapRectItem(QGraphicsRectItem):
    """A rectangle in the treemap that can be clicked."""
    
    def __init__(self, x, y, w, h, folder_info: FolderInfo, on_click=None):
        super().__init__(x, y, w, h)
        self.folder_info = folder_info
        self.on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Set colors based on depth/size
        hue = hash(folder_info.path) % 256
        color = QColor.fromHsv(hue, 200, 220)
        self.setBrush(QBrush(color))
        self.setPen(QPen(Qt.GlobalColor.black, 1))
        self.setAcceptHoverEvents(True)
    
    def mousePressEvent(self, event):
        """Handle click on rectangle."""
        if self.on_click:
            self.on_click(self.folder_info)


class TreemapWidget(QGraphicsView):
    """Widget to display treemap visualization."""
    
    folder_selected = pyqtSignal(FolderInfo)
    
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.folder_info = None
    
    def display_treemap(self, folder_info: FolderInfo):
        """Display treemap for given folder structure."""
        self.folder_info = folder_info
        self.scene.clear()
        
        if not folder_info or folder_info.total_size == 0:
            return
        
        # Calculate layout with padding
        padding = 20
        view_width = self.width() - 4 - (padding * 2)
        view_height = self.height() - 4 - (padding * 2)
        
        if view_width <= 0 or view_height <= 0:
            return
        
        self._draw_treemap(
            folder_info.children,
            padding, padding, view_width, view_height,
            folder_info.total_size
        )
    
    def _draw_treemap(self, items: List[FolderInfo], x: float, y: float,
                      width: float, height: float, total_size: int):
        """Recursively draw treemap rectangles with improved layout."""
        if not items or width <= 0 or height <= 0:
            return
        
        # Sort items by size (largest first) for better layout
        sorted_items = sorted(items, key=lambda item: item.total_size, reverse=True)
        
        # Calculate if we should use horizontal or vertical layout
        use_horizontal = width >= height
        
        current_x = x
        current_y = y
        remaining_width = width
        remaining_height = height
        spacing = 5  # Space between rectangles
        
        for idx, item in enumerate(sorted_items):
            size_ratio = item.total_size / total_size if total_size > 0 else 0
            
            if use_horizontal:
                # Horizontal layout
                item_width = width * size_ratio
                item_height = height
                
                rect_item = TreemapRectItem(current_x, current_y, item_width - spacing, item_height, item,
                                           on_click=self._on_folder_click)
                self.scene.addItem(rect_item)
                
                # Add text label
                if item_width > 40 and item_height > 30:
                    folder_name = Path(item.path).name[:15]
                    size_str = format_size(item.total_size)
                    
                    text = self.scene.addText(f"{folder_name}\n{size_str}")
                    text.setPos(current_x + 5, current_y + 5)
                    
                    font = text.font()
                    font.setPointSize(7)
                    text.setFont(font)
                    text.setTextWidth(item_width - 10)
                
                current_x += item_width
            else:
                # Vertical layout
                item_width = width
                item_height = height * size_ratio
                
                rect_item = TreemapRectItem(current_x, current_y, item_width, item_height - spacing, item,
                                           on_click=self._on_folder_click)
                self.scene.addItem(rect_item)
                
                # Add text label
                if item_width > 40 and item_height > 30:
                    folder_name = Path(item.path).name[:15]
                    size_str = format_size(item.total_size)
                    
                    text = self.scene.addText(f"{folder_name}\n{size_str}")
                    text.setPos(current_x + 5, current_y + 5)
                    
                    font = text.font()
                    font.setPointSize(7)
                    text.setFont(font)
                    text.setTextWidth(item_width - 10)
                
                current_y += item_height
    
    def _on_folder_click(self, folder_info: FolderInfo):
        """Emit signal when folder is clicked."""
        self.folder_selected.emit(folder_info)
    
    def resizeEvent(self, event):
        """Redraw treemap when window is resized."""
        super().resizeEvent(event)
        if self.folder_info:
            self.display_treemap(self.folder_info)


# ============================================================================
# Main Application Window
# ============================================================================

class DiskAnalyzerApp(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Disk Analyzer")
        self.setGeometry(100, 100, 1200, 700)
        
        # Set application icon (use .ico for Windows taskbar)
        icon_path = Path(__file__).parent / "disk_analyzer_icon.ico"
        self.icon = None
        if icon_path.exists():
            self.icon = QIcon(str(icon_path))
            self.setWindowIcon(self.icon)
        
        self.showMaximized()  # Start maximized
        
        self.current_folder: Optional[FolderInfo] = None
        self.scanner_worker: Optional[ScannerWorker] = None
        
        self._setup_ui()
        self._setup_tray_icon()
        self._connect_signals()
    
    def _setup_ui(self):
        """Setup user interface."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout: horizontal split between left and right panels
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)
        
        # ====== LEFT PANEL ======
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_panel.setLayout(left_layout)
        
        # Folder selection section
        self.select_folder_btn = QPushButton("Select Folder")
        self.select_folder_btn.setFixedHeight(100)
        left_layout.addWidget(self.select_folder_btn)
        
        # Current folder label
        self.current_folder_label = QLabel("No folder selected")
        left_layout.addWidget(self.current_folder_label)
        
        # Tree view
        self.tree_view = QTreeWidget()
        self.tree_view.setHeaderLabels(["Contents"])
        self.tree_view.setColumnCount(1)
        self.tree_view.setMinimumWidth(200)
        left_layout.addWidget(self.tree_view)
        
        # ====== RIGHT PANEL ======
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_panel.setLayout(right_layout)
        
        # Progress bar (at top of right panel)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(0)
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)
        
        # Details table
        self.details_table = QTableWidget()
        self.details_table.setColumnCount(3)
        self.details_table.setHorizontalHeaderLabels(["Name", "Size", "Percentage"])
        self.details_table.setSelectionBehavior(self.details_table.SelectionBehavior.SelectRows)
        # Set fixed widths for Size and Percentage columns
        self.details_table.setColumnWidth(1, 150)
        self.details_table.setColumnWidth(2, 150)
        # Configure resize modes: Name stretches, others stay fixed
        self.details_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.details_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.details_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        # Table takes 2/3 of right panel height
        right_layout.addWidget(self.details_table, 2)
        
        # Treemap visualization takes 1/3 of right panel height
        self.treemap_widget = TreemapWidget()
        right_layout.addWidget(self.treemap_widget, 1)
        
        # Add panels to main layout (left is 1/3 of right, so 1:3 ratio)
        main_layout.addWidget(left_panel, 1)
        main_layout.addWidget(right_panel, 3)
        
        # Status bar
        self.statusBar().showMessage("Ready")
    
    def _connect_signals(self):
        """Connect UI signals to slots."""
        self.select_folder_btn.clicked.connect(self._on_select_folder)
        self.tree_view.itemClicked.connect(self._on_tree_item_clicked)
        self.treemap_widget.folder_selected.connect(self._on_treemap_folder_selected)
    
    def _setup_tray_icon(self):
        """Setup system tray icon."""
        # Load icon from file
        icon_path = Path(__file__).parent / "disk_analyzer_icon.ico"
        if not icon_path.exists():
            print(f"Warning: Icon file not found at {icon_path}")
            return
        
        tray_icon_obj = QIcon(str(icon_path))
        
        # Create tray icon
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(tray_icon_obj)
        
        # Create context menu for tray icon
        tray_menu = QMenu(self)
        
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self.showNormal)
        
        hide_action = tray_menu.addAction("Hide")
        hide_action.triggered.connect(self.hide)
        
        tray_menu.addSeparator()
        
        exit_action = tray_menu.addAction("Exit")
        exit_action.triggered.connect(self.close)
        
        self.tray_icon.setContextMenu(tray_menu)
        
        # Show tray icon
        self.tray_icon.show()
        
        print("System tray icon loaded successfully")
        
        # Handle double-click on tray icon to show/hide window
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
    
    def _on_tray_icon_activated(self, reason):
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
    
    def _on_select_folder(self):
        """Handle folder selection."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Analyze",
            os.path.expanduser("~")
        )
        
        if folder:
            self._scan_folder(folder)
    
    def _scan_folder(self, path: str):
        """Start scanning a folder."""
        # Stop any existing scan
        if self.scanner_worker and self.scanner_worker.isRunning():
            self.scanner_worker.stop()
            self.scanner_worker.wait()
        
        # Create and start worker
        self.scanner_worker = ScannerWorker(path)
        self.scanner_worker.progress.connect(self._on_scan_progress)
        self.scanner_worker.finished.connect(self._on_scan_finished)
        self.scanner_worker.error.connect(self._on_scan_error)
        
        self.progress_bar.setVisible(True)
        self.select_folder_btn.setEnabled(False)
        self.statusBar().showMessage("Scanning...")
        
        self.scanner_worker.start()
    
    def _on_scan_progress(self, message: str):
        """Update progress label."""
        self.statusBar().showMessage(message)
    
    def _on_scan_finished(self, folder_info: FolderInfo):
        """Handle scan completion."""
        self.current_folder = folder_info
        self._display_folder(folder_info)
        
        self.progress_bar.setVisible(False)
        self.select_folder_btn.setEnabled(True)
        self.statusBar().showMessage("Scan complete")
    
    def _on_scan_error(self, error: str):
        """Handle scan error."""
        self.statusBar().showMessage(f"Error: {error}")
        self.progress_bar.setVisible(False)
        self.select_folder_btn.setEnabled(True)
    
    def _display_folder(self, folder_info: FolderInfo):
        """Display folder structure in tree view and table."""
        self.tree_view.clear()
        self.details_table.setRowCount(0)
        
        if not folder_info:
            return
        
        # Update folder label
        folder_name = Path(folder_info.path).name or folder_info.path
        self.current_folder_label.setText(f"📁 {folder_name}")
        
        # Create root item for tree
        root_item = self._create_tree_item(folder_info, folder_info.total_size)
        self.tree_view.addTopLevelItem(root_item)
        root_item.setExpanded(True)
        
        # Display details table for root folder's children
        self._populate_details_table(folder_info, folder_info.total_size)
        
        # Display treemap
        self.treemap_widget.display_treemap(folder_info)
    
    def _create_tree_item(self, folder_info: FolderInfo, total_size: int) -> QTreeWidgetItem:
        """Create tree widget item from folder info."""
        folder_name = Path(folder_info.path).name or folder_info.path
        size_str = format_size(folder_info.total_size)
        percentage = calculate_percentage(folder_info.total_size, total_size)
        percentage_str = f"{percentage:.1f}%"
        
        item = QTreeWidgetItem([folder_name, size_str, percentage_str])
        item.setData(0, Qt.ItemDataRole.UserRole, folder_info)
        
        # Add children recursively
        for child in sorted(folder_info.children, key=lambda x: x.total_size, reverse=True):
            child_item = self._create_tree_item(child, total_size)
            item.addChild(child_item)
        
        return item
    
    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle tree view item click - show details for selected folder."""
        folder_info = item.data(0, Qt.ItemDataRole.UserRole)
        if folder_info:
            self._populate_details_table(folder_info, folder_info.total_size)
            
            # Update treemap to show this folder's children
            if folder_info.children:
                virtual_root = FolderInfo(
                    folder_info.path,
                    0,
                    folder_info.children
                )
                self.treemap_widget.display_treemap(virtual_root)
    
    def _populate_details_table(self, folder_info: FolderInfo, total_size: int):
        """Populate the details table with folder contents."""
        self.details_table.setRowCount(0)
        
        # Sort children by size (largest first)
        sorted_children = sorted(folder_info.children, key=lambda x: x.total_size, reverse=True)
        
        for idx, child in enumerate(sorted_children):
            self.details_table.insertRow(idx)
            
            # Name column
            child_name = Path(child.path).name or child.path
            name_item = QTableWidgetItem(child_name)
            self.details_table.setItem(idx, 0, name_item)
            
            # Size column
            size_str = format_size(child.total_size)
            size_item = QTableWidgetItem(size_str)
            size_item.setData(Qt.ItemDataRole.UserRole, child)  # Store folder info
            self.details_table.setItem(idx, 1, size_item)
            
            # Percentage column
            percentage = calculate_percentage(child.total_size, total_size)
            percentage_item = QTableWidgetItem(f"{percentage:.1f}%")
            self.details_table.setItem(idx, 2, percentage_item)
    
    def _on_treemap_folder_selected(self, folder_info: FolderInfo):
        """Handle treemap folder selection - zoom into selected folder."""
        if folder_info.children:
            # Create a virtual root with only this folder's children
            virtual_root = FolderInfo(
                folder_info.path,
                0,
                folder_info.children
            )
            self.treemap_widget.display_treemap(virtual_root)


# ============================================================================
# Application Entry Point
# ============================================================================

def main():
    """Main entry point."""
    app = QApplication(sys.argv)
    window = DiskAnalyzerApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
