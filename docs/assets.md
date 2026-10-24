# Изображения бренда

Два исходных изображения созданы встроенным ImageGen для вымышленного бренда «Плин». Не использовались фотографии чужого магазина или реальные отзывы. Рабочие исходники:

- `assets/images/hero-plyn.png` — упаковка, чашка и солнечный стол.
- `assets/images/product-plyn.png` — предметная фотография демонстрационной упаковки.

Сборка через Sharp создаёт WebP шириной 640/960/1600 для hero и 400/800 для товара. Оригиналы остаются в репозитории, оптимизированные версии используются витриной. Все демонстрационные лоты показывают одну фирменную упаковку; описание товара и помол задаются отдельно.

## Финальный prompt: hero

Use case: product-mockup. Asset: wide hero photograph for an independent Ukrainian specialty coffee shop website named ПЛИН, based in Odesa. Create a photorealistic high-end editorial still life, landscape 3:2. On an off-white sunlit stone table: one matte dark petrol-blue 250g stand-up coffee pouch with a large restrained warm-white typographic label reading exactly «ПЛИН» and a small wave motif, a handmade cream espresso cup containing coffee, a few coffee beans and folded natural linen. Bright afternoon seaside sunlight, beautiful hard architectural shadows, a small coral-orange accent. Warm cream, deep petrol, restrained terracotta palette. Camera at 35 degree angle, tactile real packaging, sophisticated independent coffee roaster art direction, subtle film grain. Composition focuses on the bag in the right half with some breathing space. Background warm ivory wall, no scenery necessary. No extra words, no claims, no people, no watermarks. Deliver the photograph itself, no website UI, no frame.

## Финальный prompt: упаковка

Use case: product-mockup. Asset: single product photograph for the Ukrainian fictional specialty coffee roaster ПЛИН. A single beautiful matte cream/off-white 250g stand-up coffee pouch, front view with a very slight three-quarter angle. Minimal dark petrol-blue typography: exactly «ПЛИН» in large Ukrainian Cyrillic letters and a small elegant double-wave motif below it. No other text. Place pouch centered on a warm pale oatmeal studio background with a soft realistic grounding shadow, generous margins on all sides. Premium editorial product photography, tactile paper texture, natural daylight, sophisticated authentic independent roaster packaging, crisp details. Portrait square composition, entire bag visible, no props, no frame, no watermark. This is a product photograph, no website UI.
