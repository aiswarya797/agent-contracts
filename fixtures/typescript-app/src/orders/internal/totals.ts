export function totalCents(values: number[]) {
  return values.reduce((sum, value) => sum + value, 0);
}
