/*
This is only a helper script used when launching PixInsight.
It will write a temporary file to signal when PixInsight is ready to receive commands.
*/

try {
    var scriptDir = "C:/Temp/PixStack";
    var filePath = scriptDir + "/launch_done.tmp";

    // Ensure the directory exists
    if (!File.directoryExists(scriptDir)) {
        File.createDirectory(scriptDir);
    }

    // Write the signal file
    let file = new File();
    file.createForWriting(filePath);
    file.outTextLn("done");
    file.close();
    console.writeln("Signal file created successfully: " + filePath);
} catch (error) {
    console.writeln("Error creating signal file: " + error.message);
}