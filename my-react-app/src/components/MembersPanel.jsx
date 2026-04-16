import { T } from "../theme";
import { NAME_LIST } from "../constants";
import { Card, PageHeader } from "./ui";

export default function MembersPanel({ files, tableData }) {
  return (
    <div style={{ animation: "fadeIn 0.4s ease" }}>
      <PageHeader title="成员管理" subtitle="查看课题组成员的文件和报销状况" />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px" }}>
        {NAME_LIST.map((name, i) => {
          const fCount = files.filter(f => f.owner === name).length;
          const records = tableData.filter(r => r["姓名/公司"] === name);
          const totalAmount = records.reduce((s, r) => s + (parseFloat(r.金额) || 0), 0);
          const pending = records.filter(r => r.状态 === "待报销").length;

          return (
            <Card key={name} style={{ padding: "16px", animation: `fadeInUp 0.3s ease ${i * 0.03}s both` }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
                <div style={{
                  width: "36px", height: "36px", borderRadius: "50%",
                  background: `linear-gradient(135deg, hsl(${i * 25},60%,50%), hsl(${i * 25 + 30},60%,45%))`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "#fff", fontWeight: 600, fontSize: "14px", flexShrink: 0,
                }}>{name[0]}</div>
                <div>
                  <div style={{ fontSize: "14px", fontWeight: 600, color: T.text }}>{name}</div>
                  <div style={{ fontSize: "11px", color: T.textMuted }}>课题组成员</div>
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
                <div style={{ padding: "8px", background: T.surface, borderRadius: T.radiusSm, textAlign: "center" }}>
                  <div style={{ fontSize: "16px", fontWeight: 700, color: T.text }}>{fCount}</div>
                  <div style={{ fontSize: "10px", color: T.textMuted }}>文件</div>
                </div>
                <div style={{ padding: "8px", background: T.surface, borderRadius: T.radiusSm, textAlign: "center" }}>
                  <div style={{ fontSize: "16px", fontWeight: 700, color: T.text }}>{records.length}</div>
                  <div style={{ fontSize: "10px", color: T.textMuted }}>记录</div>
                </div>
                <div style={{ padding: "8px", background: T.surface, borderRadius: T.radiusSm, textAlign: "center" }}>
                  <div style={{ fontSize: "14px", fontWeight: 600, color: T.warning, fontFamily: T.mono }}>¥{totalAmount.toFixed(0)}</div>
                  <div style={{ fontSize: "10px", color: T.textMuted }}>总金额</div>
                </div>
                <div style={{ padding: "8px", background: T.surface, borderRadius: T.radiusSm, textAlign: "center" }}>
                  <div style={{ fontSize: "16px", fontWeight: 700, color: pending > 0 ? T.warning : T.success }}>{pending}</div>
                  <div style={{ fontSize: "10px", color: T.textMuted }}>待报销</div>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
