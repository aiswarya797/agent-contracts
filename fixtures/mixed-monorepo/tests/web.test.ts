import { renderDashboard } from "../apps/web/src/routes";

test("renders dashboard totals", () => {
  expect(renderDashboard(12)).toBe("$12.00");
});
