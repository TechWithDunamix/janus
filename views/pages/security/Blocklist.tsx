import { RuleList, type Rule, type RuleStats } from '@/views/ui/RuleList'

export default function Blocklist({ rules, stats }: { rules: Rule[]; stats?: RuleStats }) {
  return <RuleList kind="block" rules={rules} stats={stats} />
}
