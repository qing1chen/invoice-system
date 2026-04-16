/**
 * 本地持久化存储层
 *
 * 当前基于 localStorage 实现；如需替换为 IndexedDB、远程 API
 * 或其他存储方案，只需修改本文件即可。
 */

/**
 * 读取数据
 * @param {string} key      - 存储键名
 * @param {*}      fallback - 读取失败时的默认值
 * @returns {*} 解析后的数据 或 fallback
 */
export function loadData(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

/**
 * 写入数据
 * @param {string} key  - 存储键名
 * @param {*}      data - 要序列化并存储的数据
 */
export function saveData(key, data) {
  try {
    localStorage.setItem(key, JSON.stringify(data));
  } catch (e) {
    console.error("saveData error:", e);
  }
}
