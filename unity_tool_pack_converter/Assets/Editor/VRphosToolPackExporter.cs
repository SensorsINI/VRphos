using System;
using System.IO;
using Unity.InferenceEngine;
using UnityEditor;
using UnityEngine;

/// <summary>
/// Converts an ONNX model into a VRphos tool pack (model.sentis + tool.json), using the ONNX
/// importer of Unity Inference Engine. Tool packs are loaded by the VRphos apps at run time (docs/tool_packs.md).
///
/// Interactive:   menu  VRphos > Convert ONNX to tool pack...
/// Command line:  Unity -batchmode -nographics -quit -projectPath <this project>
///                      -executeMethod VRphosToolPackExporter.ConvertFromCommandLine
///                      -onnx path/to/model.onnx -out path/to/tools/mytool [-manifest tool.json]
///
/// If no manifest is given, a tool.json next to the ONNX file is used; otherwise a template
/// for a "map" tool is written. The "engine" field is set to the Inference Engine version, because
/// the app only loads models converted with its own version.
/// </summary>
public static class VRphosToolPackExporter
{
    private const string TempFolder = "Assets/_VRphosToolImport";

    [MenuItem("VRphos/Convert ONNX to tool pack...")]
    private static void ConvertInteractive()
    {
        string onnx = EditorUtility.OpenFilePanel("ONNX model", "", "onnx");
        if (string.IsNullOrEmpty(onnx)) return;
        string outDir = EditorUtility.SaveFolderPanel("Tool pack folder", Path.GetDirectoryName(onnx),
                                                      Path.GetFileNameWithoutExtension(onnx));
        if (string.IsNullOrEmpty(outDir)) return;
        try
        {
            Convert(onnx, outDir, null);
            EditorUtility.RevealInFinder(Path.Combine(outDir, "tool.json"));
        }
        catch (Exception e)
        {
            EditorUtility.DisplayDialog("VRphos tool pack", $"Conversion failed:\n{e.Message}", "OK");
        }
    }

    /// <summary>Entry point for -executeMethod; exits with code 1 on failure.</summary>
    public static void ConvertFromCommandLine()
    {
        string onnx = Arg("-onnx"), outDir = Arg("-out"), manifest = Arg("-manifest");
        try
        {
            if (onnx == null || outDir == null)
                throw new ArgumentException("usage: -onnx <model.onnx> -out <tool folder> [-manifest <tool.json>]");
            Convert(onnx, outDir, manifest);
            EditorApplication.Exit(0);
        }
        catch (Exception e)
        {
            Debug.LogError($"[VRphosToolPackExporter] {e.Message}");
            EditorApplication.Exit(1);
        }
    }

    /// <summary>Convert onnxPath into outDir/model.sentis and write outDir/tool.json.</summary>
    public static void Convert(string onnxPath, string outDir, string manifestPath)
    {
        if (!File.Exists(onnxPath)) throw new FileNotFoundException($"ONNX file not found: {onnxPath}");
        Directory.CreateDirectory(outDir);
        Directory.CreateDirectory(TempFolder);
        string assetPath = $"{TempFolder}/{Path.GetFileNameWithoutExtension(onnxPath)}.onnx";

        try
        {
            File.Copy(onnxPath, assetPath, true);
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
            var asset = AssetDatabase.LoadAssetAtPath<ModelAsset>(assetPath);
            if (asset == null)
                throw new InvalidOperationException("Import failed. The Unity console lists unsupported ONNX operators, if any.");

            Model model = ModelLoader.Load(asset);
            ModelWriter.Save(Path.Combine(outDir, "model.sentis"), model);

            ToolManifest manifest = ReadManifest(manifestPath, onnxPath) ?? new ToolManifest
            {
                name = Path.GetFileNameWithoutExtension(onnxPath),
                description = "Converted with VRphosToolPackExporter; edit type, input and output as needed.",
            };
            manifest.model = "model.sentis";
            manifest.engine = EngineVersion();
            File.WriteAllText(Path.Combine(outDir, "tool.json"), JsonUtility.ToJson(manifest, true));
            Debug.Log($"[VRphosToolPackExporter] Wrote tool pack '{manifest.name}' ({manifest.type}) to {outDir} " +
                      $"with Inference Engine {manifest.engine}.");
        }
        finally
        {
            AssetDatabase.DeleteAsset(assetPath);
            if (AssetDatabase.IsValidFolder(TempFolder) && AssetDatabase.FindAssets("", new[] { TempFolder }).Length == 0)
                AssetDatabase.DeleteAsset(TempFolder);
        }
    }

    private static ToolManifest ReadManifest(string manifestPath, string onnxPath)
    {
        string path = manifestPath ?? Path.Combine(Path.GetDirectoryName(onnxPath) ?? "", "tool.json");
        if (!File.Exists(path)) return null;
        return JsonUtility.FromJson<ToolManifest>(File.ReadAllText(path));
    }

    private static string EngineVersion()
    {
        var info = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(Model).Assembly);
        return info != null ? info.version : "unknown";
    }

    private static string Arg(string name)
    {
        string[] args = Environment.GetCommandLineArgs();
        int i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
