// ---------------------------------------------------------------------------
// 商品图片辅助函数
// ---------------------------------------------------------------------------

/** 按分类整理的 Unsplash 图片地址，用作兜底图片 */
const CATEGORY_FALLBACK_IMAGES: Record<string, string> = {
  electronics:
    "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=400&h=400&fit=crop",
  clothing:
    "https://images.unsplash.com/photo-1551028719-00167b16eac5?w=400&h=400&fit=crop",
  home: "https://images.unsplash.com/photo-1556909114-f6e7ad7d3136?w=400&h=400&fit=crop",
  sports:
    "https://images.unsplash.com/photo-1534438327276-14e5300c3a48?w=400&h=400&fit=crop",
  books:
    "https://images.unsplash.com/photo-1512820790803-83ca734da794?w=400&h=400&fit=crop",
};

const DEFAULT_FALLBACK =
  "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=400&h=400&fit=crop";

/**
 * 返回某件商品的图片地址。
 *
 * 优先级：
 *  1. `imageUrl` —— 保存在商品记录上（种子数据里的 Unsplash 地址）
 *  2. `category` —— 按分类整理的兜底图片
 *  3. 通用商品兜底图片
 *
 * 当提供了显式的 imageUrl 时，`width` 与 `height` 参数会写入该 Unsplash
 * 地址（替换掉原有的 w/h 查询参数）。
 */
export function productImageUrl(
  productId: string,
  width = 400,
  height = 300,
  imageUrl?: string | null,
  category?: string | null,
): string {
  if (imageUrl) {
    // 用请求的尺寸替换掉 w= 与 h= 查询参数
    return imageUrl
      .replace(/w=\d+/, `w=${width}`)
      .replace(/h=\d+/, `h=${height}`);
  }

  if (category) {
    const fallback = CATEGORY_FALLBACK_IMAGES[category.toLowerCase()];
    if (fallback) {
      return fallback
        .replace(/w=\d+/, `w=${width}`)
        .replace(/h=\d+/, `h=${height}`);
    }
  }

  return DEFAULT_FALLBACK
    .replace(/w=\d+/, `w=${width}`)
    .replace(/h=\d+/, `h=${height}`);
}

/**
 * 返回某个商品分类对应的兜底图片。
 */
export function categoryImageUrl(category: string, width = 400, height = 400): string {
  const fallback =
    CATEGORY_FALLBACK_IMAGES[category.toLowerCase()] || DEFAULT_FALLBACK;
  return fallback
    .replace(/w=\d+/, `w=${width}`)
    .replace(/h=\d+/, `h=${height}`);
}
