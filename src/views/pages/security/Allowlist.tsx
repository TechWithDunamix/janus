import { RuleList, type Rule, type RuleStats } from '@/views/ui/RuleList'

export default function Allowlist({ rules, stats }: { rules: Rule[]; stats?: RuleStats }) {
  return <RuleList kind="allow" rules={rules} stats={stats} />
}
