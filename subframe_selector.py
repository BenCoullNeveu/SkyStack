#!/usr/bin/env python3
"""
SubframeSelector - Version 1 (Base fonctionnelle)
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
    
    # Restrictions selon vos spécifications
    restrictions = {
        "L": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5, "stars": 1200, "desc": "Standards élevés"},
        "R": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5, "stars": 1200, "desc": "Standards élevés"}, 
        "G": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5, "stars": 1200, "desc": "Standards élevés"},
        "B": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5, "stars": 1200, "desc": "Standards élevés"},
        "Ha": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5, "stars": 1200, "desc": "Standards élevés"},
        "OIII": {"fwhm": 3.8, "ecc": 0.65, "snr": 1.8, "stars": 800, "desc": "Standards assouplis"},
        "SII": {"fwhm": 4.2, "ecc": 0.65, "snr": 1.5, "stars": 600, "desc": "Standards les plus permissifs"}
    }
    
    if filter_type in restrictions:
        config = restrictions[filter_type]
        print(f"\n⚖️ Restrictions pour le filtre {filter_type}:")
        print(f"  • FWHM ≤ {config['fwhm']} pixels")
        print(f"  • Excentricité ≤ {config['ecc']}")
        print(f"  • SNR ≥ {config['snr']}")
        print(f"  • Étoiles ≥ {config['stars']}")
        print(f"  • Description: {config['desc']}")
        print(f"\nConfiguration équipement:")
        print(f"  • Télescope: Skywatcher Esprit 100 ED (550mm)")
        print(f"  • Caméra: ZWO ASI2600MM (Gain 100)")
        print(f"  • Échantillonnage: 1.22 arcsec/pixel")
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
    
    # Simulation réaliste basée sur vos restrictions
    import random
    
    approved = []
    rejected = []
    
    print("\n📊 Analyse en cours...")
    print("     Fichier                           FWHM   Ecc   SNR   État")
    print("-" * 70)
    
    for i, file in enumerate(files):
        filename = os.path.basename(file)[:25]  # Tronquer le nom
        
        # Simulation avec des valeurs réalistes
        fwhm = round(random.uniform(2.2, 5.5), 1)
        eccentricity = round(random.uniform(0.2, 0.8), 2)
        snr = round(random.uniform(1.0, 6.0), 1)
        
        # Appliquer les restrictions selon le filtre
        restrictions = {
            "L": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "R": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "G": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "B": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "Ha": {"fwhm": 3.0, "ecc": 0.65, "snr": 3.5},
            "OIII": {"fwhm": 3.8, "ecc": 0.65, "snr": 1.8},
            "SII": {"fwhm": 4.2, "ecc": 0.65, "snr": 1.5}
        }
        
        limits = restrictions.get(filter_type, restrictions["L"])
        
        # Vérifier si l'image passe les critères
        passes_fwhm = fwhm <= limits["fwhm"]
        passes_ecc = eccentricity <= limits["ecc"]
        passes_snr = snr >= limits["snr"]
        
        if passes_fwhm and passes_ecc and passes_snr:
            approved.append(file)
            status = "✅ APPROVED"
        else:
            rejected.append(file)
            status = "❌ REJECTED"
        
        print(f"{i+1:2d}. {filename:<25} {fwhm:4.1f}  {eccentricity:4.2f}  {snr:4.1f}   {status}")
    
    total = len(files)
    approval_rate = (len(approved) / total * 100) if total > 0 else 0
    
    print("\n" + "=" * 70)
    print(f"📈 RÉSULTATS FINAUX:")
    print(f"  Total analysé: {total} images")
    print(f"  Approuvés: {len(approved)} images ({approval_rate:.1f}%)")
    print(f"  Rejetés: {len(rejected)} images ({100-approval_rate:.1f}%)")
    
    # Conseils selon le taux d'approbation
    if approval_rate >= 85:
        print(f"  🎉 Excellent taux d'approbation ! Session de qualité.")
    elif approval_rate >= 70:
        print(f"  👍 Bon taux d'approbation. Conditions correctes.")
    elif approval_rate >= 50:
        print(f"  ⚠️ Taux moyen. Vérifiez les conditions d'acquisition.")
    else:
        print(f"  🔧 Taux faible. Problèmes possibles: seeing, suivi, focus.")
    
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
        print(f"  📁 Approved/  - {len(approved)} images validées")
        print(f"  📁 Rejected/  - {len(rejected)} images rejetées")
        print(f"  📁 Reports/   - Rapports d'analyse (à venir)")
        print("")
        print("⚠️  NOTE: Ceci est une SIMULATION de traitement")
        print("   La prochaine version utilisera PixInsight SubframeSelector")
        print("")
        print("🚀 PROCHAINES AMÉLIORATIONS:")
        print("   • Lecture des headers FITS (température, gain)")
        print("   • Interface graphique moderne")  
        print("   • Intégration PixInsight CLI")
        print("   • Génération de rapports HTML avec graphiques")
        print("")
        
        # IMPORTANT: Empêcher la fermeture automatique
        input("🎯 Appuyez sur Entrée pour fermer le programme...")
        
        return True
        
    except KeyboardInterrupt:
        print("\n\n❌ Arrêt demandé par l'utilisateur")
        return False
    except Exception as e:
        print(f"\n❌ ERREUR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Démarrage du script...")
    success = main()
    
    if success:
        print("✅ Script terminé avec succès")
        sys.exit(0)
    else:
        print("❌ Script terminé avec des erreurs")
        input("Appuyez sur Entrée pour fermer...")  # Sécurité supplémentaire
        sys.exit(1)
