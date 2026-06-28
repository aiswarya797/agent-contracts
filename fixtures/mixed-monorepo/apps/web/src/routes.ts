import { formatCurrency } from "../../../packages/shared/src/index";

export function renderDashboard(total: number) {
  return formatCurrency(total);
}
