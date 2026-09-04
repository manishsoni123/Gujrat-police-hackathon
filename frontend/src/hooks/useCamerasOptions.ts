/** Cached lookups used by filters: cameras (id → name), departments, districts. */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { camerasApi, settingsApi } from '@/api';
import type { Camera, Department } from '@/api/types';

export function useCameraOptions(): { cameras: Camera[]; options: { value: number; label: string }[]; loading: boolean; byId: Map<number, Camera> } {
  const q = useQuery({
    queryKey: ['cameras', 'options'],
    queryFn: () => camerasApi.list({ page_size: 200, sort: 'name', order: 'asc' }),
    staleTime: 60_000,
  });
  const cameras = useMemo(() => q.data?.items ?? [], [q.data]);
  const options = useMemo(() => cameras.map((c) => ({ value: c.id, label: `${c.name} (#${c.external_id})` })), [cameras]);
  const byId = useMemo(() => new Map(cameras.map((c) => [c.id, c])), [cameras]);
  return { cameras, options, loading: q.isLoading, byId };
}

export function useDepartments(): { departments: Department[]; options: { value: number; label: string }[]; loading: boolean } {
  const q = useQuery({
    queryKey: ['departments'],
    queryFn: async () => {
      try {
        const r = await camerasApi.departments();
        return r.items;
      } catch {
        // Fallback: derive from the camera list when /departments is absent.
        const cams = await camerasApi.list({ page_size: 200 });
        const map = new Map<number, Department>();
        cams.items.forEach((c) => map.set(c.department_id, { id: c.department_id, code: c.department_code, name: c.department_name }));
        return Array.from(map.values()).sort((a, b) => a.code.localeCompare(b.code));
      }
    },
    staleTime: 300_000,
  });
  const departments = useMemo(() => q.data ?? [], [q.data]);
  const options = useMemo(() => departments.map((d) => ({ value: d.id, label: `${d.name} (${d.code})` })), [departments]);
  return { departments, options, loading: q.isLoading };
}

export function useDistricts(): string[] {
  const { cameras } = useCameraOptions();
  return useMemo(() => Array.from(new Set(cameras.map((c) => c.district).filter((d): d is string => Boolean(d)))).sort(), [cameras]);
}

export function usePublicSettings() {
  return useQuery({ queryKey: ['settings', 'public'], queryFn: settingsApi.public, staleTime: 300_000, retry: 1 });
}
