"""Playwright screenshots + pixelmatch comparison with CSS mask support."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from PIL import Image
from pixelmatch.contrib.PIL import pixelmatch


@dataclass
class PathResult:
    path: str
    main_png: Path
    update_png: Path
    diff_png: Path
    differing_pixels: int
    total_pixels: int
    diff_pct: float
    passed: bool
    error: Optional[str] = None


@dataclass
class DiffReport:
    tolerance_pct: float
    results: list[PathResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results) and bool(self.results)

    @property
    def failed_paths(self) -> list[PathResult]:
        return [r for r in self.results if not r.passed]


# CSS injected before every screenshot to neutralise animations/transitions.
_ANTIMOTION_CSS = """
*, *::before, *::after {
  animation-duration: 0s !important;
  animation-delay: 0s !important;
  transition-duration: 0s !important;
  transition-delay: 0s !important;
  caret-color: transparent !important;
}
"""


def _masking_css(selectors: list[str]) -> str:
    if not selectors:
        return ""
    sel = ", ".join(selectors)
    # Replace masked regions with a flat gray block so tiny dynamic changes
    # (timestamps, counters) don't fail the diff.
    return f"{sel} {{ visibility: hidden !important; background: #888 !important; }}"


def _screenshot_set(
    *,
    base_url: str,
    paths: list[str],
    out_dir: Path,
    mask_selectors: list[str],
    viewport_w: int,
    viewport_h: int,
    navigation_timeout: int = 45000,
    mask_wait: int = 250,
) -> dict[str, Path]:
    """Screenshot each path under base_url into out_dir/<slug>.png. Returns {path: file}."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    extra_css = _ANTIMOTION_CSS + _masking_css(mask_selectors)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": viewport_w, "height": viewport_h})
        page = context.new_page()
        for path in paths:
            url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
            slug = _path_slug(path)
            target = out_dir / f"{slug}.png"
            try:
                page.goto(url, wait_until="networkidle", timeout=navigation_timeout)
                page.add_style_tag(content=extra_css)
                # Give the masking CSS a tick to apply.
                page.wait_for_timeout(mask_wait)
                page.screenshot(path=str(target), full_page=True)
            except Exception as e:
                # Save a blank 1x1 so downstream doesn't crash; record via separate file.
                Image.new("RGB", (1, 1), (0, 0, 0)).save(target)
                (out_dir / f"{slug}.error.txt").write_text(f"{type(e).__name__}: {e}")
            results[path] = target
        browser.close()
    return results


def _path_slug(path: str) -> str:
    slug = path.strip("/").replace("/", "_").replace("?", "_q_").replace("&", "_a_")
    return slug or "root"


def _compare(main_png: Path, update_png: Path, diff_png: Path) -> tuple[int, int]:
    """Return (differing_pixels, total_pixels). Saves a diff heatmap to diff_png."""
    img_a = Image.open(main_png).convert("RGBA")
    img_b = Image.open(update_png).convert("RGBA")

    # Normalise dimensions: pixelmatch requires equal sizes. Pad the smaller
    # image with transparent pixels so layout growth registers as a diff.
    w = max(img_a.width, img_b.width)
    h = max(img_a.height, img_b.height)

    def pad(img: Image.Image) -> Image.Image:
        if img.size == (w, h):
            return img
        padded = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        padded.paste(img, (0, 0))
        return padded

    img_a = pad(img_a)
    img_b = pad(img_b)
    diff = Image.new("RGBA", (w, h))
    differing = pixelmatch(img_a, img_b, diff, threshold=0.1, includeAA=True)
    diff.save(diff_png)
    return differing, w * h


def run(
    *,
    main_base_url: str,
    update_base_url: str,
    paths: list[str],
    mask_selectors: list[str],
    tolerance_pct: float,
    artifacts_dir: Path,
    viewport_w: int,
    viewport_h: int,
    navigation_timeout: int = 45000,
    mask_wait: int = 250,
) -> DiffReport:
    """Screenshot both deployments at each path and compare."""
    main_dir = artifacts_dir / "screens" / "main"
    upd_dir = artifacts_dir / "screens" / "autoupdate"
    diff_dir = artifacts_dir / "screens" / "diff"
    diff_dir.mkdir(parents=True, exist_ok=True)

    main_shots = _screenshot_set(
        base_url=main_base_url, paths=paths, out_dir=main_dir,
        mask_selectors=mask_selectors, viewport_w=viewport_w, viewport_h=viewport_h,
        navigation_timeout=navigation_timeout, mask_wait=mask_wait,
    )
    upd_shots = _screenshot_set(
        base_url=update_base_url, paths=paths, out_dir=upd_dir,
        mask_selectors=mask_selectors, viewport_w=viewport_w, viewport_h=viewport_h,
        navigation_timeout=navigation_timeout, mask_wait=mask_wait,
    )

    report = DiffReport(tolerance_pct=tolerance_pct)
    for path in paths:
        slug = _path_slug(path)
        main_p = main_shots[path]
        upd_p = upd_shots[path]
        diff_p = diff_dir / f"{slug}.png"
        try:
            differing, total = _compare(main_p, upd_p, diff_p)
            pct = (differing / total) * 100 if total else 0.0
            passed = pct <= tolerance_pct
            report.results.append(PathResult(
                path=path,
                main_png=main_p, update_png=upd_p, diff_png=diff_p,
                differing_pixels=differing, total_pixels=total,
                diff_pct=pct, passed=passed,
            ))
        except Exception as e:
            report.results.append(PathResult(
                path=path,
                main_png=main_p, update_png=upd_p, diff_png=diff_p,
                differing_pixels=0, total_pixels=0, diff_pct=0.0, passed=False,
                error=f"{type(e).__name__}: {e}",
            ))
    return report
