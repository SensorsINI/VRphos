# Tool pack converter (Unity)

Minimal Unity project that converts an ONNX model into a VRphos tool pack (`model.sentis` +
`tool.json`) with Unity Inference Engine 2.6.1, the version the VRphos apps use.
See [docs/tool_packs.md](../docs/tool_packs.md) for the whole workflow.

Needs Unity 6000.2 or newer (install it with the Unity Hub). The first start imports the
Inference Engine package and takes a few minutes.

```bash
Unity -batchmode -nographics -quit -projectPath unity_tool_pack_converter \
      -executeMethod VRphosToolPackExporter.ConvertFromCommandLine \
      -onnx path/to/my_tool/model.onnx -out path/to/my_tool [-manifest tool.json] -logFile -
```

Or open the project in the editor: menu **VRphos → Convert ONNX to tool pack…**.

If no manifest is given, the `tool.json` next to the ONNX file is used; if there is none, a
template for a `map` tool is written. The converter sets `engine` in `tool.json`; the app only
loads models converted with its own Inference Engine version.
