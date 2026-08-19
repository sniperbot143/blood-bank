"""A local, offline chart: candles plus every SMC object the engine found.

Plotly writes a single self-contained HTML file. No account, no server, no
subscription -- the whole point of the free-first architecture is that you can
look at what the engine sees without paying anyone.

Everything drawn is filtered to `as_of`, so the chart shows what was knowable
at that bar and not a second later. A chart that quietly includes tomorrow's
swing is how backtests start lying to people.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from features.context import MarketContext
from imbalance.fvg import FVGDirection, FVGStatus
from liquidity.levels import Side
from orderblocks.order_blocks import OBDirection
from structure.breaks import BreakType
from structure.swings import SwingKind

COLORS = {
    "up": "#26a69a", "down": "#ef5350",
    "swing_high": "#ef5350", "swing_low": "#26a69a",
    "bos": "#42a5f5", "choch": "#ffa726", "mss": "#ab47bc",
    "buy_liquidity": "#ef5350", "sell_liquidity": "#26a69a",
    "sweep": "#ffee58",
    "ob_bull": "rgba(38,166,154,0.18)", "ob_bear": "rgba(239,83,80,0.18)",
    "fvg_bull": "rgba(66,165,245,0.14)", "fvg_bear": "rgba(255,167,38,0.14)",
    "premium": "rgba(239,83,80,0.06)", "discount": "rgba(38,166,154,0.06)",
    "entry": "#ffffff", "stop": "#ef5350", "target": "#26a69a",
}


@dataclass
class ChartOptions:
    bars: int = 400
    show_swings: bool = True
    show_breaks: bool = True
    show_liquidity: bool = True
    show_sweeps: bool = True
    show_order_blocks: bool = True
    show_fvgs: bool = True
    show_premium_discount: bool = True
    max_objects: int = 40           # per type, newest first -- keeps it readable
    title: str = ""
    template: str = "plotly_dark"


def render_chart(
    context: MarketContext,
    output: str | Path,
    *,
    as_of: int | None = None,
    options: ChartOptions | None = None,
    signal=None,
) -> Path:
    """Draw the chart and write a standalone HTML file. Returns its path."""
    import plotly.graph_objects as go

    options = options or ChartOptions()
    at = context.n_bars - 1 if as_of is None else as_of
    start = max(0, at - options.bars + 1)
    window = context.frame.iloc[start:at + 1]
    if window.empty:
        raise ValueError("nothing to plot")

    figure = go.Figure()
    figure.add_trace(go.Candlestick(
        x=window.index, open=window["open"], high=window["high"],
        low=window["low"], close=window["close"], name="price",
        increasing_line_color=COLORS["up"], decreasing_line_color=COLORS["down"],
    ))

    if options.show_premium_discount:
        _draw_premium_discount(figure, context, at)
    if options.show_order_blocks:
        _draw_order_blocks(figure, context, at, start, options)
    if options.show_fvgs:
        _draw_fvgs(figure, context, at, start, options)
    if options.show_liquidity:
        _draw_liquidity(figure, context, at, start, window, options)
    if options.show_swings:
        _draw_swings(figure, context, at, start)
    if options.show_breaks:
        _draw_breaks(figure, context, at, start)
    if options.show_sweeps:
        _draw_sweeps(figure, context, at, start)
    if signal is not None:
        _draw_signal(figure, signal, window)

    title = options.title or (
        f"{context.symbol} {context.timeframe} — as of "
        f"{context.frame.index[at]:%Y-%m-%d %H:%M} UTC"
    )
    figure.update_layout(
        title=title, template=options.template, xaxis_rangeslider_visible=False,
        height=820, margin=dict(l=40, r=40, t=110, b=40), showlegend=False,
        annotations=_panel(context, at, signal),
    )
    figure.update_xaxes(showgrid=False)
    figure.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.06)")

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(str(path), include_plotlyjs="cdn", full_html=True)
    return path


# ------------------------------------------------------------------ layers

def _draw_swings(figure, context: MarketContext, at: int, start: int) -> None:
    import plotly.graph_objects as go

    live = [s for s in context.swings.as_of(at) if s.formed_at_index >= start]
    for kind, colour, symbol in ((SwingKind.HIGH, COLORS["swing_high"], "triangle-down"),
                                 (SwingKind.LOW, COLORS["swing_low"], "triangle-up")):
        points = [s for s in live if s.kind is kind]
        if not points:
            continue
        figure.add_trace(go.Scatter(
            x=[s.formed_at for s in points], y=[s.price for s in points],
            mode="markers", marker=dict(symbol=symbol, size=9, color=colour),
            name=kind.value, hovertext=[f"{kind.value} {s.price:.5f}" for s in points],
        ))


def _draw_breaks(figure, context: MarketContext, at: int, start: int) -> None:
    colours = {BreakType.BOS: COLORS["bos"], BreakType.CHOCH: COLORS["choch"],
               BreakType.MSS: COLORS["mss"]}
    for event in context.breaks.events_known_at(at):
        if event.index < start:
            continue
        figure.add_hline(
            y=event.broken_level, line=dict(color=colours[event.type], width=1, dash="dot"),
            annotation_text=f"{event.type.value} {event.direction.value[:4]}",
            annotation_position="right",
            annotation_font=dict(size=9, color=colours[event.type]),
        )


def _draw_liquidity(figure, context: MarketContext, at: int, start: int,
                    window: pd.DataFrame, options: ChartOptions) -> None:
    pools = [p for p in context.liquidity.intact_at(at)][-options.max_objects:]
    for pool in pools:
        colour = (COLORS["buy_liquidity"] if pool.side is Side.BUY_SIDE
                  else COLORS["sell_liquidity"])
        figure.add_shape(
            type="line", x0=max(pool.confirmed_at, window.index[0]), x1=window.index[-1],
            y0=pool.price_at(at), y1=pool.price_at(at),
            line=dict(color=colour, width=1, dash="dash"), opacity=0.5,
        )


def _draw_sweeps(figure, context: MarketContext, at: int, start: int) -> None:
    import plotly.graph_objects as go

    events = [e for e in context.sweeps.known_at(at) if e.penetration_index >= start]
    if not events:
        return
    figure.add_trace(go.Scatter(
        x=[e.penetration_time for e in events], y=[e.extreme for e in events],
        mode="markers", marker=dict(symbol="x", size=11, color=COLORS["sweep"]),
        name="sweep",
        hovertext=[f"{e.type.value} {e.magnitude_atr:.2f}ATR" for e in events],
    ))


def _draw_order_blocks(figure, context: MarketContext, at: int, start: int,
                       options: ChartOptions) -> None:
    blocks = [b for b in context.order_blocks.tradeable_at(at)][-options.max_objects:]
    for block in blocks:
        colour = (COLORS["ob_bull"] if block.direction is OBDirection.BULLISH
                  else COLORS["ob_bear"])
        figure.add_shape(
            type="rect", x0=block.origin_time, x1=context.frame.index[at],
            y0=block.bottom, y1=block.top, fillcolor=colour, line=dict(width=0), layer="below",
        )


def _draw_fvgs(figure, context: MarketContext, at: int, start: int,
               options: ChartOptions) -> None:
    gaps = [g for g in context.fvgs.active_at(at)
            if g.status_at(at) is not FVGStatus.INVALIDATED][-options.max_objects:]
    for gap in gaps:
        colour = (COLORS["fvg_bull"] if gap.direction is FVGDirection.BULLISH
                  else COLORS["fvg_bear"])
        figure.add_shape(
            type="rect", x0=gap.formed_at, x1=context.frame.index[at],
            y0=gap.bottom, y1=gap.top, fillcolor=colour, line=dict(width=0), layer="below",
        )


def _draw_premium_discount(figure, context: MarketContext, at: int) -> None:
    dealing_range = context.range_at(at)
    if not dealing_range.is_valid:
        return
    equilibrium = dealing_range.equilibrium
    figure.add_hline(y=equilibrium, line=dict(color="rgba(255,255,255,0.35)", width=1),
                     annotation_text="EQ", annotation_position="left",
                     annotation_font=dict(size=9))
    figure.add_hrect(y0=equilibrium, y1=dealing_range.high, fillcolor=COLORS["premium"],
                     line_width=0, layer="below")
    figure.add_hrect(y0=dealing_range.low, y1=equilibrium, fillcolor=COLORS["discount"],
                     line_width=0, layer="below")


def _draw_signal(figure, signal, window: pd.DataFrame) -> None:
    levels = signal.candidate.levels
    if not levels.is_valid:
        return
    for price, colour, label in (
        (levels.entry, COLORS["entry"], "ENTRY"),
        (levels.stop_loss, COLORS["stop"], "SL"),
        (levels.take_profit_1, COLORS["target"], "TP1"),
        (levels.take_profit_2, COLORS["target"], "TP2"),
    ):
        figure.add_hline(y=price, line=dict(color=colour, width=1.5),
                         annotation_text=label, annotation_position="left",
                         annotation_font=dict(size=10, color=colour))


def _panel(context: MarketContext, at: int, signal) -> list[dict]:
    """The compact signal panel from SMC_DEFINITIONS §36."""
    snapshot = context.at(at)
    lines = [
        f"BIAS {snapshot.bias.value}",
        f"REGIME {snapshot.regime.key}",
        f"ZONE {snapshot.dealing_range.zone.value}",
        f"SESSION {snapshot.session or '-'}",
    ]
    if signal is not None:
        probability = signal.probability
        lines = [
            f"DECISION {signal.decision.value}",
            (f"PROB {probability.probability:.0%}" if np.isfinite(probability.probability)
             else "PROB n/a"),
            f"RELIABILITY {probability.reliability.value}",
            f"SAMPLE {probability.sample_size:,} ({probability.similarity_tier})",
            f"SCORE {signal.score.rounded}/100",
            (f"R:R 1:{signal.candidate.levels.rr1:.2f}"
             if np.isfinite(signal.candidate.levels.rr1) else "R:R n/a"),
        ] + lines
    return [dict(
        text="   |   ".join(lines), xref="paper", yref="paper", x=0, y=1.06,
        showarrow=False, align="left", font=dict(size=12, family="monospace"),
    )]
