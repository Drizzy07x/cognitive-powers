const { test, expect } = require('@playwright/test');

test('retry control exposes a user-visible state change', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Retry control' })).toBeVisible();
  await page.getByRole('button', { name: 'Retry payment' }).click();
  await expect(page.getByRole('status')).toHaveText('Attempts: 1');
});
