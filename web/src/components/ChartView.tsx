import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, TooltipComponent, TitleComponent, AriaComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { ChartConfig } from "../types";

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, TitleComponent, AriaComponent, SVGRenderer]);

export { resultChart } from "./chart-data";

export function ChartView({ chart }: { chart: ChartConfig }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current || chart.chart_type === "table" || chart.chart_type === "indicator") return;
    const instance = echarts.init(element.current, undefined, { renderer: "svg", width: element.current.clientWidth || 560, height: 320 });
    const pie = chart.chart_type === "pie";
    instance.setOption({ aria: { enabled: true }, title: { text: chart.title }, tooltip: { trigger: pie ? "item" : "axis", renderMode: "richText" },
      xAxis: pie ? undefined : { type: "category", data: chart.labels }, yAxis: pie ? undefined : { type: "value" },
      series: [{ type: pie ? "pie" : chart.chart_type, data: pie ? chart.labels.map((name, i) => ({ name, value: chart.values[i] })) : chart.values }] });
    const resize = () => instance.resize(); window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); instance.dispose(); };
  }, [chart]);
  return <div className="chart-container">
    {chart.chart_type === "indicator" ? <strong>{chart.values[0]}</strong> : chart.chart_type === "table" ? <p>请查看结果表。</p> : <div ref={element} role="img" aria-label={chart.title} style={{ height: 320, width: "100%" }} />}
    <p className="muted">{chart.reason}</p>
  </div>;
}
