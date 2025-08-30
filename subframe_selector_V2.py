#!/usr/bin/env python3
"""
SubframeSelector - Version 2
Lecture des headers FITS + Interface graphique
"""

import os
import glob
import sys
from pathlib import Path

# Interface graphique
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    print("Warning: Interface graphique non disponible")

# Lecture FITS
try:
    from astropy.io import fits
    FITS_READING = True
except ImportError:
    FITS_READING = False
    print("Warning: astropy non disponible - simulation des métadonnées")

VERSION = "2.0.0"
TITLE = "SkyStack SubframeSelector - Version GUI"

def read_fits_metadata(file_path):
    """Lit les métadonnées d'un fichier FITS"""
    
    if not FITS_READING:
        # Simulation si astropy non disponible
        import random
        return {
            'filename': os.path.basename(file_path),
            'exposure': 600.0,
            'temperature': round(random.uniform(-20.5, -19.5), 1),
            'gain': 100,
            'filter': 'Ha',  # Détection basée sur le nom
            'object': 'California Nebula',
            'date_obs': '2025-01-12T20:30:00'
        }
    
    try:
        with fits.open(file_path) as hdul:
            header = hdul[0].header
            
            # Extraire les métadonnées clés
            metadata = {
                'filename': os.path.basename(file_path),
                'exposure': float(header.get('EXPTIME', 0)),
                'temperature': float(header.get('CCD-TEMP', 0)),
                'gain': int(header.get('GAIN', 0)),
                'offset': int(header.get('OFFSET', 0)),
                'binning': int(header.get('XBINNING', 1)),
                'filter': extract_filter_from_filename(file_path),
                'object': header.get('OBJECT', 'Unknown'),
                'date_obs': header.get('DATE-OBS', 'Unknown'),
                'airmass': float(header.get('AIRMASS', 1.0))
            }
            
            return metadata
            
    except Exception as e:
        print(f"Erreur lecture FITS {os.path.basename(file_path)}: {e}")
        return None

def extract_filter_from_filename(file_path):
    """Extrait le filtre du nom de fichier"""
    filename = os.path.basename(file_path).upper()
    
    # Patterns de recherche dans l'ordre de priorité
    if '_H_' in filename or '_HA_' in filename:
        return 'Ha'
    elif '_OIII_' in filename or '_O3_' in filename:
        return 'OIII'
    elif '_SII_' in filename or '_S2_' in filename:
        return 'SII'
    elif '_L_' in filename:
        return 'L'
    elif '_R_' in filename:
        return 'R'
    elif '_G_' in filename:
        return 'G'
    elif '_B_' in filename:
        return 'B'
    
    return 'Unknown'

class SubframeSelectorGUI:
    """Interface graphique pour SubframeSelector"""
    
    def __init__(self):
        if not GUI_AVAILABLE:
            raise ImportError("tkinter non disponible")
            
        self.root = tk.Tk()
        self.setup_window()
        self.create_widgets()
        
        # Variables
        self.source_directory = ""
        self.selected_filter = None
        self.fits_files = []
        self.fits_metadata = []
        
    def setup_window(self):
        """Configuration de la fenêtre principale"""
        self.root.title(f"{TITLE} v{VERSION}")
        self.root.geometry("900x650")
        self.root.minsize(800, 550)
        
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        
        # Centrer la fenêtre
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (400)
        y = (self.root.winfo_screenheight() // 2) - (300)
        self.root.geometry(f"+{x}+{y}")
        
    def create_widgets(self):
        """Crée l'interface utilisateur"""
        
        # Frame principal avec padding
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(4, weight=1)
        
        # Titre
        title_label = ttk.Label(main_frame, text="SkyStack SubframeSelector V2", 
                               font=('Arial', 14, 'bold'))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 15))
        
        # Section dossier
        self.create_directory_section(main_frame, 1)
        
        # Section filtre
        self.create_filter_section(main_frame, 2)
        
        # Section résultats
        self.create_results_section(main_frame, 3)
        
        # Section boutons
        self.create_buttons_section(main_frame, 5)
        
    def create_directory_section(self, parent, row):
        """Section sélection de dossier"""
        
        # Label
        dir_label = ttk.Label(parent, text="Dossier source:", font=('Arial', 10, 'bold'))
        dir_label.grid(row=row, column=0, sticky=tk.W, pady=(0, 5))
        
        # Frame pour le chemin et bouton
        dir_frame = ttk.Frame(parent)
        dir_frame.grid(row=row+1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        dir_frame.columnconfigure(0, weight=1)
        
        self.dir_var = tk.StringVar(value="Aucun dossier sélectionné")
        self.dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, state='readonly')
        self.dir_entry.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 10))
        
        browse_btn = ttk.Button(dir_frame, text="Parcourir", command=self.browse_directory)
        browse_btn.grid(row=0, column=1)
        
        # Info fichiers
        self.files_info = ttk.Label(parent, text="", foreground='gray')
        self.files_info.grid(row=row+2, column=0, columnspan=2, sticky=tk.W)
        
    def create_filter_section(self, parent, row):
        """Section sélection de filtre"""
        
        filter_label = ttk.Label(parent, text="Filtre:", font=('Arial', 10, 'bold'))
        filter_label.grid(row=row, column=0, sticky=tk.W, pady=(10, 5))
        
        # Frame pour les boutons radio
        filter_frame = ttk.Frame(parent)
        filter_frame.grid(row=row+1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.filter_var = tk.StringVar()
        filters = [
            ("L", "Luminance"), ("R", "Rouge"), ("G", "Vert"), ("B", "Bleu"),
            ("Ha", "Hydrogène Alpha"), ("OIII", "Oxygène III"), ("SII", "Soufre II")
        ]
        
        for i, (code, name) in enumerate(filters):
            radio = ttk.Radiobutton(filter_frame, text=f"{code} - {name[:15]}" + ("..." if len(name) > 15 else ""), 
                                   variable=self.filter_var, value=code,
                                   command=self.on_filter_change)
            row_pos = i // 2
            col_pos = i % 2
            radio.grid(row=row_pos, column=col_pos, sticky=tk.W, padx=15, pady=3)
        
        # Restrictions
        self.restrictions_label = ttk.Label(parent, text="Sélectionnez un filtre pour voir les restrictions",
                                          foreground='gray')
        self.restrictions_label.grid(row=row+2, column=0, columnspan=2, sticky=tk.W)
        
    def create_results_section(self, parent, row):
        """Section résultats avec tableau"""
        
        results_label = ttk.Label(parent, text="Fichiers détectés:", font=('Arial', 10, 'bold'))
        results_label.grid(row=row, column=0, sticky=tk.W, pady=(10, 5))
        
        # Frame pour le tableau avec scrollbar
        table_frame = ttk.Frame(parent)
        table_frame.grid(row=row+1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        
        # Treeview pour afficher les fichiers
        columns = ('Fichier', 'Température', 'Exposition', 'Filtre', 'Objet')
        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=10)
        
        # Configuration des colonnes
        self.tree.heading('Fichier', text='Fichier')
        self.tree.heading('Température', text='Temp (°C)')
        self.tree.heading('Exposition', text='Exp (s)')
        self.tree.heading('Filtre', text='Filtre')
        self.tree.heading('Objet', text='Objet')
        
        self.tree.column('Fichier', width=350)
        self.tree.column('Température', width=80, anchor='center')
        self.tree.column('Exposition', width=80, anchor='center')
        self.tree.column('Filtre', width=60, anchor='center')
        self.tree.column('Objet', width=180)
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scrollbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Grid
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        v_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        h_scrollbar.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
    def create_buttons_section(self, parent, row):
        """Section boutons"""
        
        button_frame = ttk.Frame(parent)
        button_frame.grid(row=row, column=0, columnspan=2, pady=15)
        
        self.analyze_btn = ttk.Button(button_frame, text="Analyser les fichiers", 
                                     command=self.analyze_files, state='disabled')
        self.analyze_btn.grid(row=0, column=0, padx=5)
        
        self.process_btn = ttk.Button(button_frame, text="Lancer SubframeSelector", 
                                     command=self.process_files, state='disabled')
        self.process_btn.grid(row=0, column=1, padx=5)
        
        quit_btn = ttk.Button(button_frame, text="Quitter", command=self.root.quit)
        quit_btn.grid(row=0, column=2, padx=5)
        
    def browse_directory(self):
        """Parcourir pour sélectionner un dossier"""
        
        directory = filedialog.askdirectory(title="Sélectionner le dossier d'images FITS")
        
        if directory:
            self.source_directory = directory
            self.dir_var.set(directory)
            self.find_fits_files()
            
    def find_fits_files(self):
        """Cherche les fichiers FITS dans le dossier"""
        
        if not self.source_directory:
            return
            
        patterns = ["*.fit", "*.fits", "*.FIT", "*.FITS"]
        files = []
        
        for pattern in patterns:
            search_path = os.path.join(self.source_directory, pattern)
            files.extend(glob.glob(search_path))
            
        self.fits_files = sorted(list(set(files)))
        
        if self.fits_files:
            self.files_info.config(text=f"{len(self.fits_files)} fichiers FITS trouvés", 
                                  foreground='green')
            self.analyze_btn.config(state='normal')
        else:
            self.files_info.config(text="Aucun fichier FITS trouvé", foreground='red')
            self.analyze_btn.config(state='disabled')
            
    def analyze_files(self):
        """Analyse les métadonnées des fichiers FITS"""
        
        if not self.fits_files:
            return
            
        # Progress dialog would be nice here
        self.fits_metadata = []
        
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        processed = 0
        for file_path in self.fits_files:
            metadata = read_fits_metadata(file_path)
            if metadata:
                self.fits_metadata.append(metadata)
                
                # Ajouter au tableau
                self.tree.insert('', 'end', values=(
                    metadata['filename'][:30] + '...' if len(metadata['filename']) > 30 else metadata['filename'],
                    f"{metadata['temperature']:.1f}",
                    f"{metadata['exposure']:.0f}",
                    metadata['filter'],
                    metadata['object']
                ))
                processed += 1
                
        self.files_info.config(text=f"{processed} fichiers analysés avec succès", 
                              foreground='blue')
        self.update_ui_state()
        
    def on_filter_change(self):
        """Appelé quand le filtre change"""
        
        self.selected_filter = self.filter_var.get()
        
        # Mettre à jour les restrictions affichées
        restrictions = self.get_filter_restrictions(self.selected_filter)
        if restrictions:
            text = f"FWHM ≤ {restrictions['fwhm']}, Excentricité ≤ {restrictions['ecc']}, SNR ≥ {restrictions['snr']}"
            self.restrictions_label.config(text=text, foreground='black')
        
        self.update_ui_state()
        
    def get_filter_restrictions(self, filter_type):
        """Retourne les restrictions pour un filtre"""
        
        restrictions = {
            "L": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "R": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "G": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "B": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "Ha": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "OIII": {"fwhm": 3.8, "ecc": 0.65, "snr": 1.8},
            "SII": {"fwhm": 4.2, "ecc": 0.65, "snr": 1.5}
        }
        
        return restrictions.get(filter_type)
        
    def update_ui_state(self):
        """Met à jour l'état des boutons"""
        
        can_process = (self.fits_metadata and self.selected_filter)
        self.process_btn.config(state='normal' if can_process else 'disabled')
        
    def process_files(self):
        """Lance le traitement SubframeSelector"""
        
        if not self.fits_metadata or not self.selected_filter:
            messagebox.showwarning("Attention", "Sélectionnez un filtre et analysez les fichiers d'abord")
            return
            
        # Pour V2, on simule encore le traitement
        result = messagebox.askyesno("Confirmation", 
            f"Lancer SubframeSelector avec {len(self.fits_metadata)} fichiers et filtre {self.selected_filter}?")
            
        if result:
            messagebox.showinfo("Succès", 
                f"Traitement simulé terminé!\n\n"
                f"Fichiers: {len(self.fits_metadata)}\n"
                f"Filtre: {self.selected_filter}\n"
                f"Dossier: {os.path.basename(self.source_directory)}\n\n"
                f"V3 aura l'intégration PixInsight réelle.")
    
    def run(self):
        """Lance l'application"""
        self.root.mainloop()

def run_gui():
    """Lance l'interface graphique"""
    
    if not GUI_AVAILABLE:
        print("Interface graphique non disponible")
        return False
        
    try:
        app = SubframeSelectorGUI()
        app.run()
        return True
    except Exception as e:
        print(f"Erreur GUI: {e}")
        return False

def run_console():
    """Version console (comme V1)"""
    
    print("=" * 60)
    print(f"{TITLE} v{VERSION}")
    print("=" * 60)
    print("Interface console - Version 2")
    print("Pour l'interface graphique, installez tkinter")
    print("")
    
    # Réutiliser le code de la V1 adapté
    source_dir = input("Dossier d'images FITS: ").strip()
    
    if not os.path.exists(source_dir):
        print("Dossier introuvable")
        return False
        
    # Chercher fichiers
    patterns = ["*.fit", "*.fits", "*.FIT", "*.FITS"]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(source_dir, pattern)))
        
    if not files:
        print("Aucun fichier FITS trouvé")
        return False
        
    print(f"{len(files)} fichiers trouvés")
    
    # Analyser quelques fichiers
    print("\nAnalyse des métadonnées:")
    for i, file_path in enumerate(files[:5]):
        metadata = read_fits_metadata(file_path)
        if metadata:
            print(f"  {metadata['filename']}: {metadata['temperature']}°C, {metadata['exposure']}s, {metadata['filter']}")
            
    if len(files) > 5:
        print(f"  ... et {len(files)-5} autres")
        
    print(f"\nV2 terminée - {len(files)} fichiers analysés")
    input("Appuyez sur Entrée pour fermer...")
    return True

def main():
    """Point d'entrée principal"""
    
    print(f"Démarrage {TITLE} v{VERSION}")
    
    # Essayer l'interface graphique d'abord
    if GUI_AVAILABLE:
        print("Lancement de l'interface graphique...")
        success = run_gui()
    else:
        print("Interface graphique non disponible - Mode console")
        success = run_console()
    
    if success:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
