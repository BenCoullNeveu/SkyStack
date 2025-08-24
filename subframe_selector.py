#!/usr/bin/env python3
"""
Test simple pour débugger le problème
"""

import os
import glob

def test_filter_selection():
    print("=== TEST SÉLECTION FILTRE ===")
    
    filters = {
        "1": ("L", "Luminance"),
        "2": ("R", "Rouge"), 
        "3": ("G", "Vert"),
        "4": ("B", "Bleu"),
        "5": ("Ha", "Hydrogène Alpha"),
        "6": ("OIII", "Oxygène III"),
        "7": ("SII", "Soufre II")
    }
    
    print("\nFiltres disponibles:")
    for key, (code, name) in filters.items():
        print(f"  {key}. {code} - {name}")
    
    print("\nTest: Sélection automatique de Ha...")
    choice = "5"
    
    if choice in filters:
        filter_code, filter_name = filters[choice]
        print(f"✅ Filtre sélectionné: {filter_code} ({filter_name})")
        return filter_code
    
def test_fits_detection():
    print("\n=== TEST DÉTECTION FITS ===")
    
    source_dir = r"C:\Users\tlefort\OneDrive - L2C Experts\Desktop\ATRO\California Nebula\2025-01-12\LIGHT\H\600.00s_-20.00C_gain100_offset8"
    
    print(f"Dossier: {source_dir}")
    print(f"Existe: {os.path.exists(source_dir)}")
    
    if os.path.exists(source_dir):
        fits_files = glob.glob(os.path.join(source_dir, "*.fits"))
        print(f"Fichiers .fits: {len(fits_files)}")
        
        fit_files = glob.glob(os.path.join(source_dir, "*.fit"))
        print(f"Fichiers .fit: {len(fit_files)}")
        
        total = fits_files + fit_files
        print(f"Total: {len(total)} fichiers")
        
        if total:
            for i, file in enumerate(total[:3]):
                print(f"  {i+1}. {os.path.basename(file)}")
        
        return total
    return []

def test_directories():
    print("\n=== TEST CRÉATION DOSSIERS ===")
    
    base_dir = r"C:\temp\test_sfs"  # Dossier de test
    
    dirs = [
        os.path.join(base_dir, "SFS"),
        os.path.join(base_dir, "SFS", "Approved"),
        os.path.join(base_dir, "SFS", "Rejected"),
        os.path.join(base_dir, "SFS", "Reports")
    ]
    
    for directory in dirs:
        try:
            os.makedirs(directory, exist_ok=True)
            print(f"✅ Créé: {directory}")
        except Exception as e:
            print(f"❌ Erreur: {directory} - {e}")

def main():
    print("🚀 TEST DE DÉBOGAGE")
    print("=" * 40)
    
    try:
        # Test 1: Sélection filtre
        filter_type = test_filter_selection()
        print(f"Résultat filtre: {filter_type}")
        
        # Test 2: Détection FITS  
        fits_files = test_fits_detection()
        print(f"Fichiers trouvés: {len(fits_files)}")
        
        # Test 3: Création dossiers
        test_directories()
        
        print("\n✅ TOUS LES TESTS RÉUSSIS")
        
    except Exception as e:
        print(f"\n❌ ERREUR: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # IMPORTANT: Empêcher la fermeture
        input("\n🎯 Appuyez sur Entrée pour fermer...")

if __name__ == "__main__":
    main()
