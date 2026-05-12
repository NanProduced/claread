import { request } from './client'

interface LedgerEntryDto {
  id: string
  entry_type: string
  points: number
  bucket_type: string
  balance_after: number
  description: string
  article_title: string | null
  task_id: string | null
  metadata_json?: Record<string, unknown>
  created_at: string
}

interface LedgerListDto {
  items: LedgerEntryDto[]
  cursor: string | null
  has_more: boolean
}

export interface LedgerEntry {
  id: string
  entryType: string
  points: number
  bucketType: string
  balanceAfter: number
  description: string
  articleTitle: string | null
  taskId: string | null
  metadataJson?: Record<string, unknown>
  createdAt: string
}

export interface LedgerListResult {
  items: LedgerEntry[]
  cursor: string | null
  hasMore: boolean
}

function dtoToLedgerEntry(dto: LedgerEntryDto): LedgerEntry {
  return {
    id: dto.id,
    entryType: dto.entry_type,
    points: dto.points,
    bucketType: dto.bucket_type,
    balanceAfter: dto.balance_after,
    description: dto.description,
    articleTitle: dto.article_title,
    taskId: dto.task_id,
    metadataJson: dto.metadata_json,
    createdAt: dto.created_at,
  }
}

export async function fetchCreditLedger(params: {
  cursor?: string
  limit?: number
}): Promise<LedgerListResult> {
  const query: Record<string, string> = {}
  if (params.cursor) query.cursor = params.cursor
  if (params.limit) query.limit = String(params.limit)

  const qs = Object.entries(query).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
  const url = `/me/credit/ledger${qs ? `?${qs}` : ''}`

  const dto = await request<LedgerListDto>({ url })
  return {
    items: dto.items.map(dtoToLedgerEntry),
    cursor: dto.cursor,
    hasMore: dto.has_more,
  }
}
