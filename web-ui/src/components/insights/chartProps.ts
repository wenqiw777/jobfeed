/** Shared labels for Cloudscape cartesian charts. */
export const CHART_I18N = {
  filterLabel: "Filter data",
  filterPlaceholder: "Choose data",
  filterSelectedAriaLabel: "selected",
  legendAriaLabel: "Chart legend",
  detailPopoverDismissAriaLabel: "Close details",
  chartAriaRoleDescription: "chart",
  xAxisAriaRoleDescription: "horizontal axis",
  yAxisAriaRoleDescription: "vertical axis",
};

/** A visible non-zero chart range, even when every value is zero. */
export function countDomain(values: readonly number[]): [number, number] {
  return [0, Math.max(1, ...values)];
}
