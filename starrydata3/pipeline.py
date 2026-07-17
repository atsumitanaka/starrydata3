# -*- coding: utf-8 -*-
"""
pipeline.py — server-side digitization pipeline for Starrydata3.

Rather than reimplement AutoLineDigitizer's ML stack, this REUSES it: it drives
the same LineFormerApp (figure extraction, LineFormer digitization, ChartDete
axis calibration, legend→sample naming) headlessly and assembles a KMDS-shaped
record that Starrydata3's /api/v1/records ingest accepts.

Requires the AutoLineDigitizer source + its ML env (run Starrydata3 from the
`alddev` env, which has torch/cv2/mmdet/easyocr). Point $ALD_SRC at
AutoLineDigitizer/src; otherwise the common location is tried.

    from starrydata3.pipeline import get_pipeline
    rec = get_pipeline().process_pdf("paper.pdf")   # -> KMDS record dict
"""
import os
import re
import sys
from functools import lru_cache
from typing import Any, Dict, List, Optional

_ALD_CANDIDATES = [
    os.environ.get("ALD_SRC", ""),
    "/Users/Shared/AutoLineDigitizer/src",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "AutoLineDigitizer", "src"),
]


def _ald_src() -> Optional[str]:
    for c in _ALD_CANDIDATES:
        if c and os.path.exists(os.path.join(c, "desktop_app.py")):
            return c
    return None


def pipeline_available() -> bool:
    return _ald_src() is not None


def _pdf_doi(pdf_path: str) -> Optional[str]:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = "".join(doc[i].get_text() for i in range(min(3, len(doc))))
        doc.close()
        m = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", text)
        return m.group(0).rstrip(".,;)") if m else None
    except Exception:  # noqa: BLE001
        return None


def _downsample(pts: List[List[Any]], cap: int) -> List[List[Any]]:
    if len(pts) <= cap:
        return pts
    step = len(pts) / cap
    return [pts[int(i * step)] for i in range(cap)]


def assemble_record(doi: str, figures: List[Dict[str, Any]],
                    title: str = "") -> Dict[str, Any]:
    """Reviewed figure payloads -> a KMDS record Starrydata3 ingests.
    Each payload: {caption, x_name, x_unit, y_name, y_unit, x_log, y_log,
    series: [{label, points_data}]}. Legend labels become the publication's
    samples so the curve index links each series to a sample."""
    sample_ids: Dict[str, str] = {}

    def sample_id(label: str) -> str:
        if label not in sample_ids:
            sample_ids[label] = f"s-{len(sample_ids) + 1:03d}"
        return sample_ids[label]

    out_figs = []
    for f_no, fig in enumerate(figures, start=1):
        series = [s for s in (fig.get("series") or []) if s.get("points_data")]
        if not series:
            continue
        linked = [sample_id(s.get("label") or f"Line {j + 1}")
                  for j, s in enumerate(series)]
        graph = {
            "graph local id": f"g-{f_no:03d}",
            "graph name": fig.get("caption") or f"Figure {f_no}",
            "axes": [
                {"axis": "x", "quantity": {"term": fig.get("x_name") or "X"},
                 "unit": fig.get("x_unit") or "",
                 "scale": "log" if fig.get("x_log") else "linear",
                 "reference ticks": []},
                {"axis": "y", "quantity": {"term": fig.get("y_name") or "Y"},
                 "unit": fig.get("y_unit") or "",
                 "scale": "log" if fig.get("y_log") else "linear",
                 "reference ticks": []},
            ],
            "samples": linked,
            "digitization_data": {
                "x_axis": {"name": fig.get("x_name") or "X", "is_log": bool(fig.get("x_log"))},
                "y_axis": {"name": fig.get("y_name") or "Y", "is_log": bool(fig.get("y_log"))},
                "series": [{"label": s.get("label") or f"Line {j + 1}",
                            "points_data": s["points_data"]}
                           for j, s in enumerate(series)],
            },
        }
        out_figs.append({"figure local id": f"f-{f_no:03d}",
                         "figure name": fig.get("caption") or f"Figure {f_no}",
                         "graphs": [graph]})

    samples = [{"sample local id": sid_, "name": label,
                "components": [{"name": label}]}
               for label, sid_ in sample_ids.items()]
    pub: Dict[str, Any] = {"DOI": doi, "figures": out_figs, "samples": samples}
    if title:
        pub["title"] = title
    return {"metadata": {"publication": pub}, "materials": []}


class DigitizePipeline:
    """Loads the AutoLineDigitizer engine once and processes PDFs with it."""

    def __init__(self):
        src = _ald_src()
        if not src:
            raise RuntimeError("AutoLineDigitizer src not found (set $ALD_SRC)")
        if src not in sys.path:
            sys.path.insert(0, src)
        # importing desktop_app registers ChartDete's custom mmdet head
        from desktop_app import LineFormerApp   # noqa: E402
        self.app = LineFormerApp()
        self._loaded = False

    def _ensure_models(self):
        if self._loaded:
            return
        self.app.load_lineformer_model()
        self.app.load_chartdete_model()
        self._loaded = True

    def _claude_axis_fallback(self, img_bgr):
        """When classic tick-OCR calibration fails, run the desktop app's
        Claude axis reader ('detect axis' button) automatically. Returns an
        axis_config or None; needs a configured Anthropic key."""
        app = self.app
        saved = app.cached_plot_area
        try:
            app.axis_config = None
            app.cached_plot_area = None      # force plot-area detection for THIS figure
            cfg, notes = app.apply_vlm_axis_correction(img_bgr)
            if cfg and notes:
                print(f"[pipeline] Claude axis fallback: {notes[:100]}")
            return cfg
        except Exception as ex:  # noqa: BLE001
            print(f"[pipeline] Claude axis fallback failed: {ex}")
            return None
        finally:
            app.cached_plot_area = saved

    def analyze_pdf(self, pdf_path: str, progress=None) -> Dict[str, Any]:
        """PDF -> reviewable digitization. EVERY extracted figure is returned
        (uncalibrated ones flagged, not dropped) so a human can verify each one:
        {doi, figures: [{index, caption, image_bgr, calibrated, x_name, y_name,
        x_log, y_log, series: [{label, points_px, points_data}]}]}."""
        self._ensure_models()
        app = self.app
        figs = app.extract_pdf_figures(
            pdf_path,
            progress=(lambda n, phase: progress(f"locating figures ({phase}): {n} found"))
            if progress else None)
        if progress:
            progress(f"{len(figs)} figure(s) found")
        try:
            import legend_mapper
        except Exception:  # noqa: BLE001
            legend_mapper = None

        out: List[Dict[str, Any]] = []
        for idx, (img_bgr, meta) in enumerate(figs):
            if progress:
                progress(f"digitizing figure {idx + 1}/{len(figs)}")
            entry: Dict[str, Any] = {
                "index": idx, "caption": meta.get("caption") or f"Figure {idx + 1}",
                "detector": meta.get("source", ""),
                "image_bgr": img_bgr, "calibrated": False, "calibration": "auto",
                "x_name": "", "y_name": "", "x_log": False, "y_log": False,
                "series": [], "error": ""}
            try:
                saved = app.cached_plot_area
                try:
                    cfg, ocr = app.detect_axis_calibration(img_bgr)
                finally:
                    app.cached_plot_area = saved
                if not cfg:
                    # classic tick-OCR calibration failed — automatically let
                    # Claude read the axes (the desktop app's "detect axis" fix)
                    if progress:
                        progress(f"figure {idx + 1}: asking Claude to read the axes")
                    cfg = self._claude_axis_fallback(img_bgr)
                    if cfg:
                        entry["calibration"] = "claude"
                if not cfg:
                    entry["error"] = ("no calibratable axes detected "
                                      "(Claude axis fallback also unavailable/failed)")
                    out.append(entry)
                    continue
                line_ds = app.infer_module.get_dataseries(img_bgr, to_clean=False)
                series_px = [[[int(p["x"]), int(p["y"])] for p in line]
                             for line in line_ds if len(line)]
                ds_px = [app.downsample_points(pts) for pts in series_px]
                series = [[list(app.pixel_to_data_cfg(cfg, p[0], p[1])) for p in pts]
                          for pts in ds_px]
                keep = [(px, dpx, s) for px, dpx, s in zip(series_px, ds_px, series)
                        if len(s) >= 3]
                if not keep:
                    entry["error"] = "no digitizable curves found"
                    out.append(entry)
                    continue
                series_px = [px for px, _, _ in keep]
                ds_px = [dpx for _, dpx, _ in keep]
                series = [s for _, _, s in keep]
                x_name, y_name = "", ""
                try:
                    app.ocr_results = ocr    # get_axis_titles reads the cached OCR
                    x_name, y_name = app.get_axis_titles()
                except Exception:  # noqa: BLE001
                    pass
                names = None
                if legend_mapper is not None:
                    try:
                        mapped = legend_mapper.map_curves_to_legend(
                            img_bgr, getattr(app, "_last_detections", {}) or {},
                            series_px, app.chartdete_module.get_ocr_reader())
                        if any(mapped):
                            names = mapped
                    except Exception:  # noqa: BLE001
                        pass
                entry.update(
                    calibrated=True, x_name=x_name or "", y_name=y_name or "",
                    x_log=bool(cfg.get("xIsLogScale")), y_log=bool(cfg.get("yIsLogScale")),
                    series=[{"label": (names[j] if names and names[j] else f"Line {j + 1}"),
                             "points_px": _downsample(series_px[j], 400),
                             "points_px_ds": ds_px[j],   # for re-calibration
                             "points_data": series[j]}
                            for j in range(len(series))])
            except Exception as ex:  # noqa: BLE001
                entry["error"] = f"{type(ex).__name__}: {ex}"
                print(f"[pipeline] figure {idx + 1} failed: {ex}")
            out.append(entry)

        return {"doi": _pdf_doi(pdf_path) or "", "figures": out}

    def process_pdf(self, pdf_path: str, doi: Optional[str] = None,
                    progress=None) -> Dict[str, Any]:
        """PDF -> KMDS record with no review step: every calibrated figure is
        kept as-is (the quick path; /digitize's review flow is the default)."""
        analysis = self.analyze_pdf(pdf_path, progress=progress)
        figures = [{"caption": f["caption"], "x_name": f["x_name"],
                    "y_name": f["y_name"], "x_unit": "", "y_unit": "",
                    "x_log": f["x_log"], "y_log": f["y_log"],
                    "series": f["series"]}
                   for f in analysis["figures"] if f["calibrated"]]
        return assemble_record(doi or analysis["doi"] or "", figures)


@lru_cache(maxsize=1)
def get_pipeline() -> DigitizePipeline:
    return DigitizePipeline()
