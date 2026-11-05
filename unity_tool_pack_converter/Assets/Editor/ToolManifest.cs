using System;

// tool.json format of VRphos tool packs (copied from ToolPack.cs of the VRphos apps).
// Field names and defaults must stay identical to the app.

[Serializable]
public class ToolInputSpec
{
    public int width = 256;
    public int height = 256;
    public string channels = "rgb";              // "rgb" or "gray"
    public float[] mean = { 0f, 0f, 0f };        // applied after scaling to [0,1]
    public float[] std = { 1f, 1f, 1f };
}

[Serializable]
public class ToolOutputSpec
{
    public string activation = "none";           // "none" or "sigmoid", applied to map outputs
    public float threshold = -1f;                // map: values below are set to 0 (-1 = off)
    public bool invert = false;                  // map: use 1 - value
    public string unit = "normalized";           // stimulation: "normalized" ([0,1] x maxCurrent) or "ampere"
}

[Serializable]
public class ToolYoloSpec
{
    public float confidence = 0.4f;
    public float iou = 0.5f;
    public int[] classes = new int[0];           // COCO class ids to keep; empty = all
}

[Serializable]
public class ToolManifest
{
    public int format_version = 1;
    public string name = "";
    public string description = "";
    public string author = "";
    public string version = "";
    public string engine = "";                   // Inference Engine version used for conversion, e.g. "2.6.1"
    public string model = "model.sentis";
    public string type = "map";                  // "map", "yolo-seg" or "stimulation"
    public float rate_hz = 10f;
    public ToolInputSpec input = new ToolInputSpec();
    public ToolOutputSpec output = new ToolOutputSpec();
    public ToolYoloSpec yolo = new ToolYoloSpec();
}
