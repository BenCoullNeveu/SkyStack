/*
    Integration Script for PixInsight CLI.

    Does what WBPP does after registration, for each filter/exposure group:
      1. a quick first-pass ImageIntegration -> local normalization reference
      2. LocalNormalization of every frame against that reference (.xnml)
      3. final ImageIntegration using the LN data, PSF Signal Weight and the
         rejection algorithm chosen by Python from the frame count

    Reads configuration from C:/Temp/PixStack/params.json:
    {
      "ln_dir": "...",            // where .xnml files and LN references go
      "ln_scale": 1024,
      "groups": [
        { "name": "...", "files": [...], "output_path": ".../masterLight_....xisf",
          "rejection": "PercentileClip" | "WinsorizedSigmaClip" | "ESD" }
      ]
    }
    Writes C:/Temp/PixStack/integration_result.json with per-group status,
    then C:/Temp/PixStack/integration_complete.tmp ("done" or "error: ...").
*/

function readJson(path) {
    if (!File.exists(path)) {
        throw new Error("Missing JSON file: " + path);
    }
    return JSON.parse(File.readTextFile(path));
}

function writeText(path, text) {
    let file = new File();
    file.createForWriting(path);
    file.outTextLn(text);
    file.close();
}

function ensureDir(dir) {
    if (!File.directoryExists(dir)) {
        File.createDirectory(dir, true);
    }
}

function rejectionEnum(name) {
    switch (name) {
        case "PercentileClip": return ImageIntegration.prototype.PercentileClip;
        case "WinsorizedSigmaClip": return ImageIntegration.prototype.WinsorizedSigmaClip;
        case "LinearFit": return ImageIntegration.prototype.LinearFit;
        case "ESD": return ImageIntegration.prototype.Rejection_ESD;
        default: return ImageIntegration.prototype.WinsorizedSigmaClip;
    }
}

function integrate(files, xnmlFiles, rejection, outputPath) {
    var P = new ImageIntegration;
    P.images = files.map(function (f, i) {
        return [true, f, "", xnmlFiles ? xnmlFiles[i] : ""];
    });
    P.combination = ImageIntegration.prototype.Average;
    if (xnmlFiles) {
        P.normalization = ImageIntegration.prototype.LocalNormalization;
        P.rejectionNormalization = ImageIntegration.prototype.LocalRejectionNormalization;
        P.weightMode = ImageIntegration.prototype.PSFSignalWeight;
    } else {
        P.normalization = ImageIntegration.prototype.AdditiveWithScaling;
        P.rejectionNormalization = ImageIntegration.prototype.Scale;
        P.weightMode = ImageIntegration.prototype.PSFSignalWeight;
    }
    P.rejection = rejectionEnum(rejection);
    // Registered frames are zero outside their footprint on the canvas;
    // reject those pixels instead of averaging them in.
    P.rangeClipLow = true;
    P.rangeLow = 0.0;
    P.generateRejectionMaps = false;
    P.generate64BitResult = false;
    P.autoMemorySize = true;
    P.noGUIMessages = true;
    P.showImages = true;
    P.executeGlobal();

    let win = ImageWindow.windowById(P.integrationImageId);
    if (win.isNull) {
        throw new Error("ImageIntegration produced no image for " + outputPath);
    }
    win.saveAs(outputPath, false, false, false, false);
    win.forceClose();
}

function localNormalize(files, referencePath, lnDir, scale) {
    var P = new LocalNormalization;
    P.referencePathOrViewId = referencePath;
    P.referenceIsView = false;
    P.targetItems = files.map(function (f) { return [true, f]; });
    P.scale = scale;
    P.generateNormalizedImages = LocalNormalization.prototype.GenerateNormalizedImages_Never;
    P.generateNormalizationData = true;
    P.outputDirectory = lnDir;
    P.overwriteExistingFiles = true;
    P.noGUIMessages = true;
    P.executeGlobal();

    return files.map(function (f) {
        return lnDir + "/" + File.extractName(f) + ".xnml";
    });
}

function main() {
    Console.writeln("== Integration Script for PixInsight CLI ==");
    let configDir = "C:/Temp/PixStack/";
    let params = readJson(configDir + "params.json");
    ensureDir(params.ln_dir);

    let results = [];
    for (let g = 0; g < params.groups.length; ++g) {
        let group = params.groups[g];
        Console.writeln("\n>> Group " + group.name + ": " + group.files.length + " frames, rejection " + group.rejection);
        try {
            ensureDir(File.extractDrive(group.output_path) + File.extractDirectory(group.output_path));

            let lnRef = params.ln_dir + "/LN_Reference_" + group.name + ".xisf";
            integrate(group.files, null, group.rejection, lnRef);

            let xnml = localNormalize(group.files, lnRef, params.ln_dir, params.ln_scale);
            let usable = [], usableXnml = [];
            for (let i = 0; i < group.files.length; ++i) {
                if (File.exists(xnml[i])) {
                    usable.push(group.files[i]);
                    usableXnml.push(xnml[i]);
                } else {
                    Console.warningln("No LN data for " + group.files[i] + "; leaving it out.");
                }
            }
            if (usable.length < 3) {
                throw new Error("Fewer than 3 frames have local normalization data.");
            }

            integrate(usable, usableXnml, group.rejection, group.output_path);
            results.push({ name: group.name, status: "ok", output_path: group.output_path,
                           n_frames: usable.length });
        } catch (error) {
            Console.criticalln("Group " + group.name + " failed: " + error.message);
            results.push({ name: group.name, status: "error", error: error.message });
        }
    }
    writeText(configDir + "integration_result.json", JSON.stringify(results, null, 2));
}

try {
    main();
    writeText("C:/Temp/PixStack/integration_complete.tmp", "done");
} catch (error) {
    Console.criticalln("Integration failed: " + error.message);
    writeText("C:/Temp/PixStack/integration_complete.tmp", "error: " + error.message);
}
