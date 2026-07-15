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

    def process_pdf(self, pdf_path: str, doi: Optional[str] = None,
                    progress=None) -> Dict[str, Any]:
        """PDF -> KMDS record: every figure auto-calibrated + digitized, curves
        named from the legend. Figures with no calibratable axis are skipped."""
        self._ensure_models()
        app = self.app
        figs = app.extract_pdf_figures(pdf_path)
        if progress:
            progress(f"{len(figs)} figure(s) found")

        out_figs: List[Dict[str, Any]] = []
        g_no = 0
        try:
            import legend_mapper
        except Exception:  # noqa: BLE001
            legend_mapper = None

        for idx, (img_bgr, meta) in enumerate(figs):
            if progress:
                progress(f"digitizing figure {idx + 1}/{len(figs)}")
            try:
                saved = app.cached_plot_area
                try:
                    cfg, ocr = app.detect_axis_calibration(img_bgr)
                finally:
                    app.cached_plot_area = saved
                if not cfg:
                    continue
                line_ds = app.infer_module.get_dataseries(img_bgr, to_clean=False)
                series_px = [[[int(p["x"]), int(p["y"])] for p in line]
                             for line in line_ds if len(line)]
                series = [[list(app.pixel_to_data_cfg(cfg, p[0], p[1]))
                           for p in app.downsample_points(pts)]
                          for pts in series_px]
                keep = [(px, s) for px, s in zip(series_px, series) if len(s) >= 3]
                if not keep:
                    continue
                series_px = [p for p, _ in keep]
                series = [s for _, s in keep]
                x_name, y_name = app.get_axis_titles(ocr)
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

                g_no += 1
                graph = {
                    "graph local id": f"g-{g_no:03d}",
                    "graph name": (meta.get("caption") or f"Figure {idx + 1}"),
                    "axes": [
                        {"axis": "x", "quantity": {"term": x_name or "X"},
                         "unit": "", "scale": "log" if cfg.get("xIsLogScale") else "linear",
                         "reference ticks": []},
                        {"axis": "y", "quantity": {"term": y_name or "Y"},
                         "unit": "", "scale": "log" if cfg.get("yIsLogScale") else "linear",
                         "reference ticks": []},
                    ],
                    "samples": [],
                    "digitization_data": {
                        "x_axis": {"name": x_name or "X", "is_log": bool(cfg.get("xIsLogScale"))},
                        "y_axis": {"name": y_name or "Y", "is_log": bool(cfg.get("yIsLogScale"))},
                        "series": [
                            {"label": (names[j] if names and names[j] else f"Line {j + 1}"),
                             "points_data": series[j]} for j in range(len(series))],
                    },
                }
                out_figs.append({"figure local id": f"f-{idx + 1:03d}",
                                 "figure name": meta.get("caption") or f"Figure {idx + 1}",
                                 "graphs": [graph]})
            except Exception as ex:  # noqa: BLE001
                print(f"[pipeline] figure {idx + 1} failed: {ex}")

        doi = doi or _pdf_doi(pdf_path) or ""
        return {
            "metadata": {"publication": {"DOI": doi, "figures": out_figs,
                                         "samples": []}},
            "materials": [],
        }


@lru_cache(maxsize=1)
def get_pipeline() -> DigitizePipeline:
    return DigitizePipeline()
