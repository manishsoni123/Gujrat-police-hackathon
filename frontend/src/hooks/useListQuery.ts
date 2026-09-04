/** Paginated table state (page/page_size/sort/order + filters) synced with TanStack Query. */
import { useCallback, useMemo, useState } from 'react';
import { keepPreviousData, useQuery, type UseQueryResult } from '@tanstack/react-query';
import type { TablePaginationConfig } from 'antd';
import type { SorterResult } from 'antd/es/table/interface';
import type { ListParams, Paginated } from '@/api/types';

export interface ListState {
  page: number;
  page_size: number;
  sort?: string;
  order?: 'asc' | 'desc';
}

export interface UseListQueryOptions<T> {
  key: unknown[];
  fetcher: (params: ListParams) => Promise<Paginated<T>>;
  filters?: Record<string, string | number | boolean | undefined>;
  defaultSort?: string;
  defaultOrder?: 'asc' | 'desc';
  pageSize?: number;
  enabled?: boolean;
  refetchInterval?: number | false;
}

export interface UseListQueryResult<T> {
  query: UseQueryResult<Paginated<T>>;
  items: T[];
  total: number;
  state: ListState;
  setPage: (page: number, pageSize?: number) => void;
  pagination: TablePaginationConfig;
  onTableChange: (p: TablePaginationConfig, _f: unknown, sorter: SorterResult<T> | SorterResult<T>[]) => void;
  reset: () => void;
}

export function useListQuery<T>(opts: UseListQueryOptions<T>): UseListQueryResult<T> {
  const { key, fetcher, filters, defaultSort, defaultOrder = 'desc', pageSize = 25, enabled = true, refetchInterval = false } = opts;
  const [state, setState] = useState<ListState>({ page: 1, page_size: pageSize, sort: defaultSort, order: defaultSort ? defaultOrder : undefined });
  const filterKey = JSON.stringify(filters ?? {});

  const params = useMemo<ListParams>(() => ({ ...(filters ?? {}), ...state }), [filters, state]);
  const query = useQuery({
    queryKey: [...key, filterKey, state],
    queryFn: () => fetcher(params),
    placeholderData: keepPreviousData,
    enabled,
    refetchInterval,
  });

  const setPage = useCallback((page: number, size?: number) => setState((s) => ({ ...s, page, page_size: size ?? s.page_size })), []);
  const reset = useCallback(() => setState((s) => ({ ...s, page: 1 })), []);

  const onTableChange = useCallback(
    (p: TablePaginationConfig, _f: unknown, sorter: SorterResult<T> | SorterResult<T>[]) => {
      const s = Array.isArray(sorter) ? sorter[0] : sorter;
      const sortField = s && s.order ? (typeof s.field === 'string' ? s.field : Array.isArray(s.field) ? s.field.join('.') : undefined) : undefined;
      const sortKey = s && s.order && typeof s.columnKey === 'string' ? s.columnKey : sortField;
      setState((prev) => ({
        page: p.current ?? 1,
        page_size: p.pageSize ?? prev.page_size,
        sort: sortKey ?? defaultSort,
        order: s && s.order ? (s.order === 'ascend' ? 'asc' : 'desc') : defaultSort ? defaultOrder : undefined,
      }));
    },
    [defaultSort, defaultOrder],
  );

  const total = query.data?.total ?? 0;
  const pagination: TablePaginationConfig = {
    current: state.page,
    pageSize: state.page_size,
    total,
    showSizeChanger: true,
    pageSizeOptions: [10, 25, 50, 100],
    showTotal: (t, range) => `${range[0]}–${range[1]} of ${t}`,
    size: 'small',
  };

  return { query, items: query.data?.items ?? [], total, state, setPage, pagination, onTableChange, reset };
}
