#feature-id    SubframeSelectorV3_Minimal
#feature-info  SubframeSelector Version 3 - Version minimaliste qui fonctionne<br/>Test de base avant ajout des fonctionnalités

#include <pjsr/Sizer.jsh>
#include <pjsr/FrameStyle.jsh>

// Configuration globale
var VERSION = "3.0.0-minimal";
var TITLE = "SubframeSelector V3 Test";

// Dialog minimaliste pour tester la base
function SubframeSelectorDialog() {
    this.__base__ = Dialog;
    this.__base__();
    
    Console.writeln("Construction du dialog...");
    
    // Variables
    this.sourceDirectory = "";
    
    // Interface ultra-simple
    this.infoLabel = new Label(this);
    this.infoLabel.text = "SubframeSelector V3 - Test minimal";
    this.infoLabel.wordWrapping = true;
    this.infoLabel.minWidth = 400;
    
    this.directoryEdit = new Edit(this);
    this.directoryEdit.text = "Aucun dossier sélectionné";
    this.directoryEdit.readOnly = true;
    this.directoryEdit.minWidth = 300;
    
    var self = this;
    this.browseButton = new PushButton(this);
    this.browseButton.text = "Parcourir...";
    this.browseButton.onClick = function() {
        Console.writeln("Bouton parcourir cliqué");
        self.browseDirectory();
    };
    
    this.okButton = new PushButton(this);
    this.okButton.text = "OK";
    this.okButton.onClick = function() {
        Console.writeln("OK cliqué");
        self.ok();
    };
    
    this.cancelButton = new PushButton(this);
    this.cancelButton.text = "Annuler";
    this.cancelButton.onClick = function() {
        Console.writeln("Annuler cliqué");
        self.cancel();
    };
    
    // Layout simple
    this.dirSizer = new HorizontalSizer;
    this.dirSizer.spacing = 8;
    this.dirSizer.add(this.directoryEdit);
    this.dirSizer.add(this.browseButton);
    
    this.buttonSizer = new HorizontalSizer;
    this.buttonSizer.spacing = 8;
    this.buttonSizer.addStretch();
    this.buttonSizer.add(this.okButton);
    this.buttonSizer.add(this.cancelButton);
    
    this.sizer = new VerticalSizer;
    this.sizer.margin = 8;
    this.sizer.spacing = 8;
    this.sizer.add(this.infoLabel);
    this.sizer.add(this.dirSizer);
    this.sizer.addStretch();
    this.sizer.add(this.buttonSizer);
    
    this.windowTitle = TITLE;
    this.adjustToContents();
    
    Console.writeln("Dialog construit avec succès");
}

SubframeSelectorDialog.prototype = new Dialog;

SubframeSelectorDialog.prototype.browseDirectory = function() {
    Console.writeln("Ouverture du sélecteur de dossier...");
    
    try {
        var dialog = new GetDirectoryDialog;
        dialog.caption = "Sélectionner le dossier d'images FITS";
        
        if (dialog.execute()) {
            this.sourceDirectory = dialog.directory;
            this.directoryEdit.text = this.sourceDirectory;
            Console.writeln("Dossier sélectionné: " + this.sourceDirectory);
            
            // Test simple de lecture de fichiers
            this.testFileFind();
        } else {
            Console.writeln("Sélection annulée");
        }
    } catch (error) {
        Console.writeln("ERREUR browseDirectory: " + error.message);
    }
};

SubframeSelectorDialog.prototype.testFileFind = function() {
    if (!this.sourceDirectory) {
        return;
    }
    
    Console.writeln("Test de lecture du dossier: " + this.sourceDirectory);
    
    try {
        var fileCount = 0;
        var fileFind = new FileFind;
        
        if (fileFind.begin(this.sourceDirectory + "/*")) {
            do {
                if (fileFind.isFile) {
                    var fileName = fileFind.name;
                    if (fileName.toLowerCase().indexOf('.fit') >= 0) {
                        Console.writeln("  FITS trouvé: " + fileName);
                        fileCount++;
                    }
                }
            } while (fileFind.next());
            
            fileFind.end();
        }
        
        Console.writeln("Total fichiers FITS: " + fileCount);
        this.infoLabel.text = "Dossier analysé: " + fileCount + " fichiers FITS trouvés";
        
    } catch (error) {
        Console.writeln("ERREUR testFileFind: " + error.message);
        this.infoLabel.text = "Erreur: " + error.message;
    }
};

// Point d'entrée ultra-simple
function main() {
    Console.show();
    Console.writeln("=".repeat(50));
    Console.writeln("DÉMARRAGE " + TITLE + " v" + VERSION);
    Console.writeln("=".repeat(50));
    
    try {
        Console.writeln("Création du dialog...");
        var dialog = new SubframeSelectorDialog();
        
        Console.writeln("Affichage du dialog...");
        var result = dialog.execute();
        
        Console.writeln("Dialog fermé, résultat: " + result);
        
        if (result) {
            Console.writeln("✅ Script terminé normalement");
        } else {
            Console.writeln("❌ Script annulé");
        }
        
    } catch (error) {
        Console.writeln("ERREUR FATALE: " + error.message);
        Console.writeln("Stack: " + error.toString());
        
        // Afficher une MessageBox d'erreur
        try {
            var msgBox = new MessageBox(
                "Erreur fatale:\n" + error.message,
                TITLE,
                StdIcon_Error
            );
            msgBox.execute();
        } catch (e) {
            Console.writeln("Impossible d'afficher la MessageBox: " + e.message);
        }
    }
    
    Console.writeln("=".repeat(50));
    Console.writeln("FIN DU SCRIPT");
    Console.writeln("=".repeat(50));
}

// Vérifications avant lancement
Console.writeln("Script chargé, vérification de l'environnement...");
Console.writeln("Version PixInsight: " + coreVersionString);

// Lancer seulement en mode manuel
main();
