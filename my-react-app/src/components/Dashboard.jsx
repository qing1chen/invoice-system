import { T } from "../theme";
import { NAME_LIST } from "../constants";
import { fileIcon } from "../utils/helpers";
import { Card, Badge, EmptyState, PageHeader } from "./ui";

/**
 * 概览仪表盘
 *
 * 顶部展示 4 个统计卡片，底部展示最近上传的文件列表。
 * 管理员看到全局数据，普通用户只看到自己的数据。
 *
 * @param {object} user      - 当前用户 { name, role }
 * @param {Array}  files     - 全部文件数组
 * @param {Array}  tableData - 全部报销记录数组
 */
export default function Dashboard({ user, files, tableData }) {
  const isAdmin = user.role === "admin";
  const myFiles = isAdmin ? files : files.filter((f) => f.owner === user.name);
  const myRecords = isAdmin
    ? tableData
    : tableData.filter((r) => r["姓名/公司"] === user.name);
  const totalAmount = myRecords.reduce(
    (s, r) => s + (parseFloat(r.金额) || 0),
    0
  );

  const stats = [
    { label: "文件总数", value: myFiles.length, icon: "📄", color: T.accent },
    { label: "报销记录", value: myRecords.length, icon: "📋", color: T.info },
    {
      label: "待报销金额",
      value: `¥${totalAmount.toFixed(2)}`,
      icon: "💰",
      color: T.warning,
    },
    {
      label: isAdmin ? "成员总数" : "文件类别",
      value: isAdmin
        ? NAME_LIST.length
        : [...new Set(myFiles.map((f) => f.category))].length,
      icon: isAdmin ? "👥" : "🏷️",
      color: T.success,
    },
  ];

  return (
    <div style={{ animation: "fadeIn 0.4s ease" }}>
      <PageHeader
        title={isAdmin ? "管理控制台" : `欢迎回来，${user.name}`}
        subtitle={
          isAdmin
            ? "发票报销管理系统全局概览 · MCP Server 后端"
            : "查看您的报销文件和记录"
        }
      />

      {/* 统计卡片 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: "16px",
          marginBottom: "28px",
        }}
      >
        {stats.map((s, i) => (
          <Card
            key={i}
            style={{
              padding: "18px 20px",
              animation: `fadeInUp 0.4s ease ${i * 0.08}s both`,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: "12px",
                    color: T.textMuted,
                    marginBottom: "8px",
                  }}
                >
                  {s.label}
                </div>
                <div
                  style={{ fontSize: "24px", fontWeight: 700, color: T.text }}
                >
                  {s.value}
                </div>
              </div>
              <div
                style={{
                  width: "40px",
                  height: "40px",
                  borderRadius: "10px",
                  background: `${s.color}15`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "20px",
                }}
              >
                {s.icon}
              </div>
            </div>
          </Card>
        ))}
      </div>

      {/* 最近文件 */}
      <Card>
        <div
          style={{
            fontSize: "15px",
            fontWeight: 600,
            color: T.text,
            marginBottom: "16px",
          }}
        >
          最近上传的文件
        </div>
        {!myFiles.length ? (
          <EmptyState icon="📭" title="暂无文件" subtitle="上传发票文件开始使用" />
        ) : (
          <div style={{ display: "grid", gap: "8px" }}>
            {myFiles
              .slice(-5)
              .reverse()
              .map((f, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "12px",
                    padding: "10px 14px",
                    background: T.surface,
                    borderRadius: T.radiusSm,
                    animation: `slideInRight 0.3s ease ${i * 0.05}s both`,
                  }}
                >
                  <span style={{ fontSize: "20px" }}>{fileIcon(f.name)}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: "13px",
                        color: T.text,
                        fontWeight: 500,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {f.name}
                    </div>
                    <div style={{ fontSize: "11px", color: T.textMuted }}>
                      {f.owner} · {f.uploadTime}
                    </div>
                  </div>
                  <Badge>{f.category || "未分类"}</Badge>
                </div>
              ))}
          </div>
        )}
      </Card>
    </div>
  );
}
