#target photoshop

// ==========================================================================
// BulkComposite-Auto — نسخهٔ «بدونِ دیالوگ» برای اجرای خودکار روی ایستگاهِ طراحی.
//
// این فایل فورکِ V22 است. **منطقِ تصویری دست‌نخورده است**: همان Magic Wand بومی،
// همان مختصات، همان سایه، همان اعتبارسنجی‌های سخت. تنها چیزی که عوض شده «پوسته» است:
//   • هیچ alert / Folder.selectDialog / File.openDialog ای وجود ندارد.
//   • مسیرها از یک فایلِ تنظیماتِ ساده (key=value) خوانده می‌شود.
//   • قالب‌ها خودش باز می‌کند (اگر باز نباشند).
//   • خروجی به‌صورتِ خطوطِ ماشین‌خوان در یک فایلِ نتیجه نوشته می‌شود.
//   • کار «بچ‌بچ» انجام می‌شود (max_jobs) تا اجراکننده بتواند پیشرفت را ببیند و
//     فتوشاپ را هر چند بچ یک‌بار تازه کند.
//
// فایلِ تنظیمات: کنارِ همین اسکریپت، به نامِ  job.ini  (UTF-8) —
//   hero_template=C:\psagent\templates\hero.psd
//   gallery_template=C:\psagent\templates\gallery.psd
//   input=C:\psagent\inbox
//   output=C:\psagent\outbox
//   max_jobs=60
//
// فایلِ نتیجه: کنارِ همین اسکریپت، به نامِ  result.tsv  (UTF-8) —
//   OK      <TAB> filename
//   SKIP    <TAB> filename
//   FAIL    <TAB> filename <TAB> reason
//   STATS   <TAB> ok <TAB> skipped <TAB> failed <TAB> remaining
//
// مقدارِ بازگشتیِ اسکریپت هم همان خطِ STATS است (برای DoJavaScriptFile).
// ==========================================================================

var V22_CONFIG = {
    whiteTolerance: 32,        // JPEG-safe tolerance around the sampled corner background color.
    expandSelectionPx: 1,      // Helps eliminate thin white fringe.
    featherSelectionPx: 0.5,   // Softens the cut edge very slightly.
    minTrimPx: 2,              // Validation: at least this much total trim must occur.
    jpegQuality: 10,
    purgeEvery: 10             // هر چند کار یک‌بار کش را خالی کن (ضدِ تورمِ حافظه در بچ‌های بلند).
};

// --------------------------------------------------------------------------
// SHELL: config / result / entry point
// --------------------------------------------------------------------------
function scriptFolder() {
    return File($.fileName).parent;
}

function readConfig() {
    var f = new File(scriptFolder().fsName + "/job.ini");
    if (!f.exists) throw new Error("job.ini not found next to the script.");
    f.encoding = "UTF8";
    f.open("r");
    var cfg = {};
    while (!f.eof) {
        var line = f.readln();
        if (!line) continue;
        line = line.replace(/^\uFEFF/, "");
        if (line.charAt(0) === "#" || line.charAt(0) === ";") continue;
        var eq = line.indexOf("=");
        if (eq < 1) continue;
        var k = trimStr(line.substring(0, eq));
        var v = trimStr(line.substring(eq + 1));
        if (k) cfg[k] = v;
    }
    f.close();
    return cfg;
}

function trimStr(s) {
    return String(s).replace(/^[\s\uFEFF]+/, "").replace(/[\s\uFEFF]+$/, "");
}

function openTemplate(path, label) {
    var wanted = new File(path);
    if (!wanted.exists) throw new Error("Template not found (" + label + "): " + path);
    for (var i = 0; i < app.documents.length; i++) {
        try {
            if (app.documents[i].fullName.fsName === wanted.fsName) return app.documents[i];
        } catch (ignore) {}
    }
    return app.open(wanted);
}

function main() {
    var cfg = readConfig();

    var inputFolder = new Folder(cfg.input);
    var outputFolder = new Folder(cfg.output);
    if (!inputFolder.exists) throw new Error("Input folder not found: " + cfg.input);
    if (!outputFolder.exists && !outputFolder.create()) {
        throw new Error("Cannot create output folder: " + cfg.output);
    }
    var maxJobs = parseInt(cfg.max_jobs, 10);
    if (!maxJobs || maxJobs < 1) maxJobs = 60;

    var heroTemplateDoc = openTemplate(cfg.hero_template, "hero");
    var galleryTemplateDoc = openTemplate(cfg.gallery_template, "gallery");

    var originalRulerUnits = app.preferences.rulerUnits;
    var originalDialogs = app.displayDialogs;
    app.preferences.rulerUnits = Units.PIXELS;
    app.displayDialogs = DialogModes.NO;

    var successFiles = [];
    var skippedFiles = [];
    var failedFiles = [];
    var remaining = 0;

    try {
        var jobQueue = buildQueue(inputFolder, outputFolder, skippedFiles);

        var processed = 0;
        for (var k = 0; k < jobQueue.length; k++) {
            if (processed >= maxJobs) { remaining = jobQueue.length - k; break; }
            var job = jobQueue[k];
            var saveFile = new File(outputFolder.fsName + "/" + job.saveName);

            try {
                if (job.type === "HERO") {
                    processHero(job.file, heroTemplateDoc, saveFile);
                } else {
                    processGallery(job.file, galleryTemplateDoc, saveFile);
                }
                successFiles.push(job.saveName);
            } catch (jobError) {
                // Safety guarantee: if anything failed, do not leave a partial output behind.
                try {
                    if (saveFile.exists) saveFile.remove();
                } catch (removeErr) {}
                failedFiles.push([job.saveName, safeError(jobError)]);
            }

            processed++;
            if (V22_CONFIG.purgeEvery > 0 && processed % V22_CONFIG.purgeEvery === 0) {
                try { app.purge(PurgeTarget.ALLCACHES); } catch (purgeErr) {}
            }
        }

        return writeResult(successFiles, skippedFiles, failedFiles, remaining);

    } finally {
        app.displayDialogs = originalDialogs;
        app.preferences.rulerUnits = originalRulerUnits;
    }
}

// صف‌سازی — دقیقاً همان گروه‌بندیِ V22: «{x}-{N}» فقط وقتی پسوندِ گالری است که خودِ «{x}» هم
// در پوشه باشد؛ وگرنه کلِ نام یک رفرنسِ مستقل است (رفرنس‌هایی مثلِ 16-6018-13-007 نباید بشکنند).
function buildQueue(inputFolder, outputFolder, skippedFiles) {
    var watchFiles = inputFolder.getFiles(function(f) {
        return (f instanceof File) &&
               f.name.match(/\.(jpg|jpeg|png|webp)$/i) &&
               !f.name.match(/^logo\./i);
    });
    if (watchFiles.length === 0) return [];

    var fileData = [];
    var nameSet = {};

    for (var a = 0; a < watchFiles.length; a++) {
        var nameNoExtA = watchFiles[a].name.replace(/\.[^\.]+$/, "");
        nameSet[nameNoExtA] = true;
    }

    for (var b = 0; b < watchFiles.length; b++) {
        var f = watchFiles[b];
        var nameNoExt = f.name.replace(/\.[^\.]+$/, "");
        var baseRef = nameNoExt;
        var suffixNum = 0;

        var match = nameNoExt.match(/^(.+)-(\d+)$/);
        if (match) {
            var potentialBase = match[1];
            var potentialSuffix = parseInt(match[2], 10);
            if (nameSet[potentialBase]) {
                baseRef = potentialBase;
                suffixNum = potentialSuffix;
            }
        }

        fileData.push({
            file: f,
            nameNoExt: nameNoExt,
            baseRef: baseRef,
            suffixNum: suffixNum,
            isBase: (suffixNum === 0)
        });
    }

    var groups = {};
    for (var c = 0; c < fileData.length; c++) {
        var d = fileData[c];
        if (!groups[d.baseRef]) groups[d.baseRef] = [];
        groups[d.baseRef].push(d);
    }

    var raw = [];
    for (var ref in groups) {
        var gFiles = groups[ref];
        gFiles.sort(function(x, y) { return x.suffixNum - y.suffixNum; });

        for (var j = 0; j < gFiles.length; j++) {
            var item = gFiles[j];
            if (item.isBase) {
                raw.push({ file: item.file, type: "HERO",    saveName: item.baseRef + ".jpg" });
                raw.push({ file: item.file, type: "GALLERY", saveName: item.baseRef + "-1.jpg" });
            } else {
                var newSuffix = item.suffixNum + 1;
                raw.push({ file: item.file, type: "GALLERY", saveName: item.baseRef + "-" + newSuffix + ".jpg" });
            }
        }
    }

    // کارهایی که خروجی‌شان از قبل هست، همین‌جا کنار می‌روند تا بچ‌بندی «کارِ واقعی» را بشمارد
    // و اجرای دوباره از همان‌جا که مانده بود ادامه پیدا کند.
    var queue = [];
    for (var q = 0; q < raw.length; q++) {
        var out = new File(outputFolder.fsName + "/" + raw[q].saveName);
        if (out.exists) { skippedFiles.push(raw[q].saveName); continue; }
        queue.push(raw[q]);
    }
    return queue;
}

function writeResult(successFiles, skippedFiles, failedFiles, remaining) {
    var stats = "STATS\t" + successFiles.length + "\t" + skippedFiles.length +
                "\t" + failedFiles.length + "\t" + remaining;
    try {
        var res = new File(scriptFolder().fsName + "/result.tsv");
        res.encoding = "UTF8";
        res.open("w");
        for (var i = 0; i < successFiles.length; i++) res.writeln("OK\t" + successFiles[i]);
        for (var j = 0; j < skippedFiles.length; j++) res.writeln("SKIP\t" + skippedFiles[j]);
        for (var k = 0; k < failedFiles.length; k++) {
            res.writeln("FAIL\t" + failedFiles[k][0] + "\t" + failedFiles[k][1]);
        }
        res.writeln(stats);
        res.close();
    } catch (e) {
        // نوشتنِ گزارش هرگز نباید روی پردازشِ عکس اثر بگذارد.
    }
    return stats;
}

// ==============================================================
// MODULE 1: HERO PROCESSING   (بدونِ تغییر نسبت به V22)
// ==============================================================
function processHero(currentFile, templateDoc, saveFile) {
    var watchDoc = null;
    var newWatchLayer = null;
    var shadowLayer = null;

    try {
        watchDoc = prepareWatchSource(currentFile);

        watchDoc.selection.selectAll();
        watchDoc.selection.copy();
        watchDoc.close(SaveOptions.DONOTSAVECHANGES);
        watchDoc = null;

        app.activeDocument = templateDoc;
        templateDoc.paste();
        newWatchLayer = templateDoc.activeLayer;
        newWatchLayer.name = "Watch_Hero";

        var targetTop = 405.594;
        var targetBottom = 1687.813;
        var targetH = targetBottom - targetTop;
        var targetCenterX = 542;

        var wBounds = newWatchLayer.bounds;
        var wW = px(wBounds[2]) - px(wBounds[0]);
        var wH = px(wBounds[3]) - px(wBounds[1]);
        if (wW <= 0 || wH <= 0) throw new Error("Invalid HERO source bounds after paste.");

        var scaleRatio = (targetH / wH) * 100;
        newWatchLayer.resize(scaleRatio, scaleRatio, AnchorPosition.MIDDLECENTER);

        wBounds = newWatchLayer.bounds;
        var dy = targetBottom - px(wBounds[3]);
        newWatchLayer.translate(0, dy);

        var bandW = 0;
        var bandCenterCurrent = 0;
        var dynamicSuccess = false;

        try {
            loadTransparencyStrict();
            var y1 = targetBottom - 20;
            var y2 = targetBottom + 5;
            var x1 = 0;
            var x2 = px(templateDoc.width);
            templateDoc.selection.select([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], SelectionType.INTERSECT, 0, false);
            var selBounds = templateDoc.selection.bounds;
            var bandLeft = px(selBounds[0]);
            var bandRight = px(selBounds[2]);
            bandW = bandRight - bandLeft;
            bandCenterCurrent = (bandLeft + bandRight) / 2;
            templateDoc.selection.deselect();
            if (bandW > 5 && bandW < px(templateDoc.width)) dynamicSuccess = true;
        } catch (bandError) {
            try { templateDoc.selection.deselect(); } catch (ignore1) {}
        }

        if (dynamicSuccess) {
            newWatchLayer.translate(targetCenterX - bandCenterCurrent, 0);
        } else {
            wBounds = newWatchLayer.bounds;
            wW = px(wBounds[2]) - px(wBounds[0]);
            bandW = wW * 0.35;
            var visualCenterX = px(wBounds[0]) + (wW * 0.46);
            newWatchLayer.translate(targetCenterX - visualCenterX, 0);
        }

        shadowLayer = templateDoc.artLayers.add();
        shadowLayer.name = "Shadow_Hero";
        shadowLayer.move(newWatchLayer, ElementPlacement.PLACEAFTER);

        var sx = targetCenterX;
        var sH = 6;
        var gap = 10;
        var sy = targetBottom + gap + (sH / 2);
        var sW = Math.max(5, bandW);
        var sBounds = [sx - sW/2, sy - sH/2, sx + sW/2, sy + sH/2];

        templateDoc.selection.select([
            [sBounds[0], sBounds[1]], [sBounds[2], sBounds[1]],
            [sBounds[2], sBounds[3]], [sBounds[0], sBounds[3]]
        ], SelectionType.REPLACE, 0, true);

        var grayColor = new SolidColor();
        grayColor.rgb.red = 45;
        grayColor.rgb.green = 45;
        grayColor.rgb.blue = 45;
        templateDoc.selection.fill(grayColor);
        templateDoc.selection.deselect();
        shadowLayer.applyGaussianBlur(7);
        shadowLayer.opacity = 55;

        saveJPEG(templateDoc, saveFile);

    } finally {
        if (watchDoc) {
            try { watchDoc.close(SaveOptions.DONOTSAVECHANGES); } catch (ignore2) {}
        }
        if (templateDoc) {
            try { app.activeDocument = templateDoc; } catch (ignore3) {}
            if (shadowLayer) try { shadowLayer.remove(); } catch (ignore4) {}
            if (newWatchLayer) try { newWatchLayer.remove(); } catch (ignore5) {}
            try { templateDoc.selection.deselect(); } catch (ignore6) {}
        }
    }
}

// ==============================================================
// MODULE 2: GALLERY PROCESSING   (بدونِ تغییر نسبت به V22)
// ==============================================================
function processGallery(currentFile, galleryDoc, saveFile) {
    var watchDoc = null;
    var newWatchLayer = null;

    try {
        watchDoc = prepareWatchSource(currentFile);

        watchDoc.selection.selectAll();
        watchDoc.selection.copy();
        watchDoc.close(SaveOptions.DONOTSAVECHANGES);
        watchDoc = null;

        app.activeDocument = galleryDoc;
        galleryDoc.paste();
        newWatchLayer = galleryDoc.activeLayer;
        newWatchLayer.name = "Watch_Gallery";

        var targetTop = 64.375;
        var targetBottom = 896.563;
        var targetH = targetBottom - targetTop;
        var targetCenterX = 512;
        var targetCenterY = (targetTop + targetBottom) / 2;

        var wBounds = newWatchLayer.bounds;
        var wW = px(wBounds[2]) - px(wBounds[0]);
        var wH = px(wBounds[3]) - px(wBounds[1]);
        if (wW <= 0 || wH <= 0) throw new Error("Invalid GALLERY source bounds after paste.");

        var scaleRatio = (targetH / wH) * 100;
        newWatchLayer.resize(scaleRatio, scaleRatio, AnchorPosition.MIDDLECENTER);

        wBounds = newWatchLayer.bounds;
        wW = px(wBounds[2]) - px(wBounds[0]);
        var visualCenterX = px(wBounds[0]) + (wW * 0.46);
        var currentCenterY = (px(wBounds[1]) + px(wBounds[3])) / 2;

        newWatchLayer.translate(targetCenterX - visualCenterX, targetCenterY - currentCenterY);

        saveJPEG(galleryDoc, saveFile);

    } finally {
        if (watchDoc) {
            try { watchDoc.close(SaveOptions.DONOTSAVECHANGES); } catch (ignore1) {}
        }
        if (galleryDoc) {
            try { app.activeDocument = galleryDoc; } catch (ignore2) {}
            if (newWatchLayer) try { newWatchLayer.remove(); } catch (ignore3) {}
            try { galleryDoc.selection.deselect(); } catch (ignore4) {}
        }
    }
}

// ==============================================================
// SOURCE PREPARATION - NATIVE, NO AI   (بدونِ تغییر نسبت به V22)
// ==============================================================
function prepareWatchSource(currentFile) {
    var doc = app.open(currentFile);
    app.activeDocument = doc;

    try {
        if (doc.layers.length !== 1) {
            // Flatten only the temporary opened source document, never the original file on disk.
            doc.flatten();
        }

        if (doc.activeLayer.isBackgroundLayer) {
            doc.activeLayer.isBackgroundLayer = false;
        }

        var originalW = px(doc.width);
        var originalH = px(doc.height);

        // Select the contiguous outer background from a safe corner sample.
        selectContiguousBackgroundAtCorner(doc, V22_CONFIG.whiteTolerance);

        // Optional edge cleanup against JPEG white halos.
        if (V22_CONFIG.expandSelectionPx > 0) {
            try { doc.selection.expand(V22_CONFIG.expandSelectionPx); } catch (ignoreExpand) {}
        }
        if (V22_CONFIG.featherSelectionPx > 0) {
            try { doc.selection.feather(V22_CONFIG.featherSelectionPx); } catch (ignoreFeather) {}
        }

        // Delete selected OUTER background pixels -> real transparency.
        doc.selection.clear();
        doc.selection.deselect();

        // Hard validation #1: transparency must exist and foreground must be selectable.
        loadTransparencyStrict();
        var fgBounds = doc.selection.bounds; // throws when foreground selection is empty
        var fgLeft = px(fgBounds[0]);
        var fgTop = px(fgBounds[1]);
        var fgRight = px(fgBounds[2]);
        var fgBottom = px(fgBounds[3]);
        doc.selection.deselect();

        if (fgRight <= fgLeft || fgBottom <= fgTop) {
            throw new Error("Background removal validation failed: no foreground pixels remain.");
        }

        // A successful outer-background removal should expose transparency at least on one edge.
        var insetSum = fgLeft + fgTop + (originalW - fgRight) + (originalH - fgBottom);
        if (insetSum < V22_CONFIG.minTrimPx) {
            throw new Error("Background removal validation failed: no meaningful transparent border detected.");
        }

        // Trim actual transparent pixels and validate dimensions changed.
        doc.trim(TrimType.TRANSPARENT);
        var trimmedW = px(doc.width);
        var trimmedH = px(doc.height);
        var trimmedTotal = (originalW - trimmedW) + (originalH - trimmedH);

        if (trimmedTotal < V22_CONFIG.minTrimPx) {
            throw new Error("Background removal validation failed: transparency did not trim the canvas.");
        }

        return doc;

    } catch (e) {
        try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (closeErr) {}
        throw e;
    }
}

// Selects only pixels connected to the sampled TOP-LEFT outer background.
// This is intentionally NOT Color Range: white details/reflections inside the watch remain protected.
function selectContiguousBackgroundAtCorner(doc, tolerance) {
    app.activeDocument = doc;

    var x = Math.min(2, Math.max(0, px(doc.width) - 1));
    var y = Math.min(2, Math.max(0, px(doc.height) - 1));

    var setID = charIDToTypeID("setd");
    var desc = new ActionDescriptor();
    var ref = new ActionReference();
    ref.putProperty(charIDToTypeID("Chnl"), charIDToTypeID("fsel"));
    desc.putReference(charIDToTypeID("null"), ref);

    var point = new ActionDescriptor();
    point.putUnitDouble(charIDToTypeID("Hrzn"), charIDToTypeID("#Pxl"), x);
    point.putUnitDouble(charIDToTypeID("Vrtc"), charIDToTypeID("#Pxl"), y);
    desc.putObject(charIDToTypeID("T   "), charIDToTypeID("Pnt "), point);
    desc.putInteger(charIDToTypeID("Tlrn"), tolerance);
    desc.putBoolean(charIDToTypeID("AntA"), true);
    desc.putBoolean(charIDToTypeID("Cntg"), true);

    executeAction(setID, desc, DialogModes.NO);

    // Verify a real selection exists before deleting anything.
    try {
        var b = doc.selection.bounds;
        if (px(b[2]) <= px(b[0]) || px(b[3]) <= px(b[1])) {
            throw new Error("Empty background selection.");
        }
    } catch (e) {
        throw new Error("Could not select the outer white background natively. " + safeError(e));
    }
}

function loadTransparencyStrict() {
    var desc = new ActionDescriptor();
    var ref = new ActionReference();
    ref.putProperty(charIDToTypeID("Chnl"), charIDToTypeID("fsel"));
    desc.putReference(charIDToTypeID("null"), ref);

    var ref2 = new ActionReference();
    ref2.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"), charIDToTypeID("Trsp"));
    desc.putReference(charIDToTypeID("T   "), ref2);
    executeAction(charIDToTypeID("setd"), desc, DialogModes.NO);
}

function saveJPEG(doc, saveFile) {
    var jpegOptions = new JPEGSaveOptions();
    jpegOptions.quality = V22_CONFIG.jpegQuality;
    doc.saveAs(saveFile, jpegOptions, true, Extension.LOWERCASE);

    if (!saveFile.exists) {
        throw new Error("JPEG save validation failed: output file was not created.");
    }
}

function px(v) {
    try { return v.as("px"); } catch (e) { return Number(v); }
}

function safeError(e) {
    if (!e) return "Unknown error";
    var msg = "";
    try { msg = e.message; } catch (ignore1) {}
    if (!msg) {
        try { msg = e.toString(); } catch (ignore2) { msg = "Unknown error"; }
    }
    try {
        if (e.line) msg += " [line " + e.line + "]";
    } catch (ignore3) {}
    return String(msg).replace(/[\r\n\t]+/g, " ");
}

// مقدارِ بازگشتی = خطِ STATS (یا FATAL). اجراکننده همین را می‌خواند.
(function () {
    try {
        return main();
    } catch (fatal) {
        return "FATAL\t" + safeError(fatal);
    }
})();
