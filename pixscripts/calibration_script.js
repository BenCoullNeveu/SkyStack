/*
    Calibration Script for PixInsight CLI.
    Reads configuration from JSON files:
    - input_files.json: array of input file paths
    - params.json: { master_flat: "...", master_dark: "...", output_dir:
*/

function readJson(path) {
    if (!File.exists(path)) {
        throw new Error("Missing JSON file: " + path);
    }
    let text = File.readTextFile(path);
    return JSON.parse(text);
}

function main() {
    Console.writeln("== Calibration Script for PixInsight CLI ==");

    let configDir = "C:/Temp/PixStack/";
    let inputFilePath = configDir + "input_files.json";
    let paramFilePath = configDir + "params.json";

    // Load input light files
    let inputFiles = readJson(inputFilePath);
    if (!inputFiles || inputFiles.length < 1) {
        Console.writeln("Error: No input files found.");
        Console.criticalln("Error: Need at least one input file.");
        exit(1);
    }
    Console.writeln("Input files loaded: " + inputFiles.length);
    Console.writeln("First input file: " + inputFiles[0]);


    // Load master calibration files and output directory
    let params = readJson(paramFilePath);
    let masterFlatPath = params.master_flat;
    let masterDarkPath = params.master_dark;
    let outputDir = params.output_dir;
    let prefix = params.prefix;

    // Console.writeln("Input files:");
    // inputFiles.forEach(f => Console.writeln(" - " + f));

    // Set up ImageCalibration
    var P = new ImageCalibration;

    P.targetFrames = inputFiles.map(function (f) {
        return [
            true, // enabled
            f, // path
        ];
    });

    Console.writeln(P.targetFrames.length + " target frames loaded.");
    Console.writeln("Master flat: " + masterFlatPath);
    Console.writeln("Master dark: " + masterDarkPath);


    // Master dark and flat parameters
    P.masterBiasEnabled = false;
    P.masterDarkEnabled = true;
    P.masterFlatEnabled = true;
    P.masterDarkPath = masterDarkPath;
    P.masterFlatPath = masterFlatPath;

    // Calibration parameters   
    P.calibrationMode = ImageCalibration.prototype.CalibrateAndCorrect;
    P.darkOptimization = false;
    P.flatNormalization = ImageCalibration.prototype.Median;
    P.useDarksForBias = false;
    P.useFlatsForBias = false;
    P.enableCFA = false

    // Cosmetic correction parameters
    P.cosmeticCorrection = false;
    P.cosmeticCorrectionHigh = false;

    // Noise eval parameters
    P.evaluateNoise = true;
    P.noiseEvaluationAlgorithm = ImageCalibration.prototype.NoiseEvaluation_MRS;
    P.evaluateSignal = true;
    
    // psf parameters
    P.psfType = ImageCalibration.prototype.PSFType_Moffat4;

    // output parameters
    P.outputDirectory = outputDir;
    P.overwriteExistingFiles = true;
    P.outputPostfix = prefix;

    // Execute
    Console.writeln(">> Running ImageCalibration...");
    P.executeGlobal();

    Console.writeln(">> ImageCalibration completed successfully.");
    Console.writeln("Integrated images saved to: " + outputDir);
}

// Run the main function!
main();