#!/usr/bin/env python3
"""
SubframeSelector - Version 1 (Base)
Script de test pour SkyStack - fonctionnalités de base
"""

import os
import glob
import sys
from pathlib import Path

VERSION = "1.0.0"
TITLE = "SkyStack SubframeSelector - Version Simple"

def find_fits_files(directory):
    """Trouve les fichiers FITS dans un dossier"""
    
    print(f"🔍 Recherche des fichiers FITS dans: {directory}")
    
    if not os.path.exists(directory):
        print(f"❌ Erreur: Le dossier n'existe pas: {directory}")
        return []
    
    # Patterns de recherche
    patterns = ["*.fit", "*.fits", "*.FIT", "*.FITS"]
    files = []
    
    for pattern in patterns:
        search_path = os.path.join(directory, pattern)
        found_files = glob.glob(search_path)
        files.extend(found_files)
        
    # Enlever les doublons et trier
    files = sorted(list(set(files)))
    
    print(f"📊 Résultat: {len(files)} fichiers FITS trouvés")
    
    # Afficher les premiers fichiers
    for i, file in enumerate(files[:5]):
        file_size = os.path.getsize(file) / (1024 * 1024)  # MB
        print(f"  {i+1}. {os.path.basename(file)} ({file_size:.1f} MB)")
    
    if len(files) > 5:
        print(f"  ... et {len(files)-5} autres fichiers")
        
    return files

def get_filter_choice():
    """Interface simple pour choisir le filtre"""
    
    filters = {
        "1": ("L", "Luminance"),
        "2": ("R", "Rouge"), 
        "3": ("G", "Vert"),
        "4": ("B", "Bleu"),
        "5": ("Ha", "Hydrogène Alpha"),
        "6": ("OIII", "Oxygène III"),
        "7": ("SII", "Soufre II")
    }
    
    print("\n🔍 Sélection du filtre:")
    print("-" * 30)
    for key, (code, name) in filters.items():
        print(f"  {key}. {code} - {name}")
    print("-" * 30)
    
    while True:
        try:
            choice = input("Votre choix (1-7): ").strip()
            
            if choice in filters:
                filter_code, filter_name = filters[choice]
                print(f"✅ Filtre sélectionné: {filter_code} ({filter_name})")
                return filter_code
            else:
                print("❌ Choix invalide. Tapez un chiffre entre 1 et 7.")
                
        except KeyboardInterrupt:
            print("\n❌ Arrêt demandé par l'utilisateur")
            return None
        except Exception as e:
            print(f"❌ Erreur: {e}")
            return None

def show_filter_restrictions(filter_type):
    """Affiche les restrictions pour le filtre choisi"""
    
    # Restrictions simplifiées pour le test
    restrictions = {
        "L": {"fwhm": 3.0, "snr": 3.5, "desc": "Standards élevés"},
        "R": {"fwhm": 3.0, "snr": 3.5, "desc": "Standards élevés"}, 
        "G": {"fwhm": 3.0, "snr": 3.5, "desc": "Standards élevés"},
        "B": {"fwhm": 3.0, "snr": 3.5, "desc": "Standards élevés"},
        "Ha": {"fwhm": 3.0, "snr": 3.5, "desc": "Standards élevés"},
        "OIII": {"fwhm": 3.8, "snr": 1.8, "desc": "Standards assouplis"},
        "SII": {"fwhm": 4.2, "snr": 1.5, "desc": "Standards les plus permissifs"}
    }
    
    if filter_type in restrictions:
        config = restrictions[filter_type]
        print(f"\n⚖️ Restrictions pour le filtre {filter_type}:")
        print(f"  • FWHM ≤ {config['fwhm']} pixels")
        print(f"  • SNR ≥ {config['snr']}")
        print(f"  • Description: {config['desc']}")
    else:
        print(f"⚠️ Filtre {filter_type} non reconnu")

def create_output_directories(source_dir):
    """Crée les dossiers de sortie"""
    
    sfs_dir = os.path.join(source_dir, "SFS")
    approved_dir = os.path.join(sfs_dir, "Approved")
    rejected_dir = os.path.join(sfs_dir, "Rejected") 
    reports_dir = os.path.join(sfs_dir, "Reports")
    
    directories = [sfs_dir, approved_dir, rejected_dir, reports_dir]
    
    print(f"\n📁 Création des dossiers de sortie...")
    
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            print(f"  ✅ {os.path.basename(directory)}/")
        except Exception as e:
            print(f"  ❌ Erreur création {directory}: {e}")
            return False
    
    print(f"\n📂 Structure créée dans: {sfs_dir}")
    return sfs_dir

def simulate_processing(files, filter_type):
    """Simule le traitement SubframeSelector"""
    
    print(f"\n🚀 Simulation du traitement SubframeSelector...")
    print(f"Filtre: {filter_type}")
    print(f"Fichiers à analyser: {len(files)}")
    
    # Simulation simple
    import random
    
    approved = []
    rejected = []
    
    print("\n📊 Analyse en cours...")
    
    for i, file in enumerate(files):
        filename = os.path.basename(file)
        
        # Simulation aléatoire (70% de chances d'approbation)
        if random.random() < 0.7:
            approved.append(file)
            status = "✅ APPROVED"
        else:
            rejected.append(file)
            status = "❌ REJECTED"
            
        print(f"  {i+1:2d}. {filename[:30]:<30} {status}")
    
    total = len(files)
    approval_rate = (len(approved) / total * 100) if total > 0 else 0
    
    print(f"\n📈 RÉSULTATS:")
    print(f"  Total analysé: {total}")
    print(f"  Approuvés: {len(approved)} ({approval_rate:.1f}%)")
    print(f"  Rejetés: {len(rejected)} ({100-approval_rate:.1f}%)")
    
    return approved, rejected

def main():
    """Fonction principale"""
    
    print("=" * 60)
    print(f"🌟 {TITLE}")
    print("=" * 60)
    print("Version de test - Fonctionnalités de base")
    print("Équipement: Skywatcher Esprit 100 ED + ZWO ASI2600MM")
    print("")
    
    try:
        # Étape 1: Demander le dossier source
        print("ÉTAPE 1: Sélection du dossier source")
        print("-" * 40)
        
        while True:
            source_dir = input("📁 Chemin vers le dossier d'images: ").strip()
            
            if not source_dir:
                print("❌ Veuillez entrer un chemin")
                continue
                
            if os.path.exists(source_dir):
                print(f"✅ Dossier trouvé: {source_dir}")
                break
            else:
                print(f"❌ Dossier introuvable: {source_dir}")
                retry = input("Réessayer ? (o/n): ").lower()
                if retry != 'o':
                    print("Arrêt du programme")
                    return False
        
        # Étape 2: Chercher les fichiers FITS
        print(f"\nÉTAPE 2: Recherche des fichiers FITS")
        print("-" * 40)
        
        fits_files = find_fits_files(source_dir)
        
        if not fits_files:
            print("❌ Aucun fichier FITS trouvé dans ce dossier")
            print("Vérifiez que les fichiers ont l'extension .fit ou .fits")
            return False
        
        # Étape 3: Sélection du filtre
        print(f"\nÉTAPE 3: Sélection du filtre")
        print("-" * 40)
        
        filter_type = get_filter_choice()
        
        if not filter_type:
            print("❌ Aucun filtre sélectionné")
            return False
            
        show_filter_restrictions(filter_type)
        
        # Étape 4: Créer les dossiers de sortie
        print(f"\nÉTAPE 4: Préparation des dossiers")
        print("-" * 40)
        
        sfs_dir = create_output_directories(source_dir)
        if not sfs_dir:
            print("❌ Impossible de créer les dossiers de sortie")
            return False
        
        # Étape 5: Simulation du traitement
        print(f"\nÉTAPE 5: Traitement (SIMULATION)")
        print("-" * 40)
        
        approved, rejected = simulate_processing(fits_files, filter_type)
        
        # Résumé final
        print(f"\n🎉 TRAITEMENT TERMINÉ")
        print("=" * 60)
        print(f"Résultats sauvegardés dans: {sfs_dir}")
        print(f"  📁 Approved/  - {len(approved)} images")
        print(f"  📁 Rejected/  - {len(rejected)} images")
        print(f"  📁 Reports/   - Rapports d'analyse")
        print("")
        print("⚠️  NOTE: Ceci est une SIMULATION de traitement")
        print("   La vraie version utilisera PixInsight SubframeSelector")
        print("")
        
        return True
        
    except KeyboardInterrupt:
        print("\n\n❌ Arrêt demandé par l'utilisateur")
        return False
    except Exception as e:
        print(f"\n❌ ERREUR: {str(e)}")
        return False

if __name__ == "__main__":
    print("🚀 Démarrage du script...")
    success = main()
    
    if success:
        print("✅ Script terminé avec succès")
        sys.exit(0)
    else:
        print("❌ Script terminé avec des erreurs")
        sys.exit(1)
