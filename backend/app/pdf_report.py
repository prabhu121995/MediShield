"""
Small PDF report builder (fpdf2) shared by the log and audit analysis downloads:
title and scope line, stat tiles, a stacked-column chart, and tables.

Chart marks follow the same spec as the web charts: one y-axis, hairline solid gridlines,
thin columns with a small surface gap between stacked segments, a legend with every series
and its total. fpdf's core fonts are Latin-1, so all text passes through latin().
"""

from __future__ import annotations

from typing import Sequence

INK, MUTED, GRID = (11, 11, 11), (82, 81, 78), (225, 224, 220)


def latin(text) -> str:
    return str(text).replace("—", "-").replace("–", "-").replace("→", "->") \
        .encode("latin-1", "replace").decode("latin-1")


def rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class Report:
    def __init__(self, title: str, scope: str):
        from fpdf import FPDF
        self.pdf = FPDF(orientation="P", unit="mm", format="A4")
        self.pdf.set_auto_page_break(auto=True, margin=15)
        self.pdf.add_page()
        p = self.pdf
        p.set_font("Helvetica", "B", 16)
        p.set_text_color(*INK)
        p.cell(0, 9, latin(title), new_x="LMARGIN", new_y="NEXT")
        p.set_font("Helvetica", "", 9)
        p.set_text_color(*MUTED)
        p.cell(0, 6, latin(scope), new_x="LMARGIN", new_y="NEXT")
        p.ln(3)

    @property
    def width(self) -> float:
        return self.pdf.w - self.pdf.l_margin - self.pdf.r_margin

    def heading(self, text: str) -> None:
        self.pdf.set_font("Helvetica", "B", 11)
        self.pdf.set_text_color(*INK)
        self.pdf.cell(0, 7, latin(text), new_x="LMARGIN", new_y="NEXT")

    def tiles(self, items: Sequence[tuple[str, str]]) -> None:
        p = self.pdf
        n = len(items)
        w = (self.width - (n - 1) * 3) / n
        y = p.get_y()
        for i, (label, value) in enumerate(items):
            x = p.l_margin + i * (w + 3)
            p.set_draw_color(*GRID)
            p.rect(x, y, w, 18)
            p.set_xy(x + 2, y + 2)
            p.set_font("Helvetica", "", 7)
            p.set_text_color(*MUTED)
            p.cell(w - 4, 4, latin(label))
            p.set_xy(x + 2, y + 8)
            p.set_font("Helvetica", "B", 13)
            p.set_text_color(*INK)
            p.cell(w - 4, 7, latin(value))
        p.set_y(y + 24)

    def stacked_columns(self, title: str, points: list[dict], series: list[tuple[str, str, str]],
                        totals: dict[str, int], label_of=lambda b: b) -> None:
        """points: [{"bucket": ..., key: count}]; series: [(key, label, hex)] drawn bottom -> top."""
        p = self.pdf
        self.heading(title)
        pts = points[-48:]
        chart_x, chart_w, chart_h = p.l_margin + 10, self.width - 10, 50
        top = p.get_y() + 2
        base = top + chart_h
        peak = max([sum(pt.get(k, 0) for k, _, _ in series) for pt in pts] or [1]) or 1
        p.set_draw_color(*GRID)
        p.set_font("Helvetica", "", 6)
        p.set_text_color(*MUTED)
        for frac in (0, 0.5, 1):
            yy = base - frac * chart_h
            p.line(chart_x, yy, chart_x + chart_w, yy)
            p.set_xy(p.l_margin, yy - 2)
            p.cell(9, 4, latin(f"{int(peak * frac):,}"), align="R")
        if pts:
            slot = chart_w / len(pts)
            bw = min(6.0, slot * 0.7)
            for i, pt in enumerate(pts):
                x = chart_x + i * slot + (slot - bw) / 2
                yy = base
                for key, _, color in series:
                    if pt.get(key):
                        hgt = pt[key] / peak * chart_h
                        p.set_fill_color(*rgb(color))
                        p.rect(x, yy - hgt, bw, max(hgt - 0.3, 0.2), style="F")
                        yy -= hgt
            for i in sorted({0, len(pts) // 2, len(pts) - 1}):
                p.set_xy(chart_x + i * slot - 8, base + 1)
                p.cell(slot + 16, 4, latin(label_of(pts[i]["bucket"])), align="C")
        p.set_y(base + 7)
        p.set_font("Helvetica", "", 8)
        for key, label, color in reversed(series):          # legend top-of-stack first
            p.set_fill_color(*rgb(color))
            p.rect(p.get_x(), p.get_y() + 1, 3, 3, style="F")
            p.set_x(p.get_x() + 4)
            p.set_text_color(*INK)
            p.cell(self.width / max(4, len(series)) - 4, 5, latin(f"{label}  {totals.get(key, 0):,}"))
        p.ln(9)

    def table(self, title: str, headers: list[str], fractions: list[float], rows: list[list]) -> None:
        p = self.pdf
        widths = [self.width * f for f in fractions]
        self.heading(title)
        p.set_font("Helvetica", "B", 8)
        p.set_fill_color(245, 245, 243)
        for h, wd in zip(headers, widths):
            p.cell(wd, 6, latin(h), border="B", fill=True)
        p.ln()
        p.set_font("Helvetica", "", 8)
        if not rows:
            p.set_text_color(*MUTED)
            p.cell(0, 6, "No data in this range.", new_x="LMARGIN", new_y="NEXT")
        p.set_text_color(*INK)
        for r in rows:
            for value, wd in zip(r, widths):
                text = latin(value)
                while p.get_string_width(text) > wd - 1 and len(text) > 3:
                    text = text[:-4] + "..."
                p.cell(wd, 5.5, text, border="B")
            p.ln()
        p.ln(4)

    def entries(self, title: str, items: list[tuple[str, str]]) -> None:
        """A list of (heading line, wrapped text) pairs, for notes too long for a table cell."""
        p = self.pdf
        self.heading(title)
        if not items:
            p.set_font("Helvetica", "", 8)
            p.set_text_color(*MUTED)
            p.cell(0, 6, "No data in this range.", new_x="LMARGIN", new_y="NEXT")
        for head, text in items:
            p.set_font("Helvetica", "B", 8)
            p.set_text_color(*MUTED)
            p.multi_cell(0, 4.5, latin(head), new_x="LMARGIN", new_y="NEXT")
            p.set_font("Helvetica", "", 8)
            p.set_text_color(*INK)
            p.multi_cell(0, 4, latin(text), new_x="LMARGIN", new_y="NEXT")
            p.set_draw_color(*GRID)
            p.line(p.l_margin, p.get_y() + 1, p.l_margin + self.width, p.get_y() + 1)
            p.ln(2.5)
        p.ln(3)

    def note(self, text: str) -> None:
        self.pdf.set_font("Helvetica", "", 7)
        self.pdf.set_text_color(*MUTED)
        self.pdf.multi_cell(0, 4, latin(text))

    def output(self) -> bytes:
        return bytes(self.pdf.output())
