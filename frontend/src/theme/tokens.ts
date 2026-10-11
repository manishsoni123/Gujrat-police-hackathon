import type { ThemeConfig } from 'antd';
import { BRAND } from './colours';

export const FONT_FAMILY =
  '"Inter", -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif';
export const MONO_FAMILY = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

/**
 * Ant Design 5 theme. Visual language follows BoardUI (boardui.com/components): neutral-950 text, neutral-200
 * hairlines, white surfaces on a neutral-50 canvas, blue accent with gradient primary buttons (global.css),
 * 8 px control radius and soft layered shadows. Amended in CONTRACT §11.3 (2026-10-11).
 */
export const antTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorSuccess: BRAND.success,
    colorWarning: BRAND.warning,
    colorError: BRAND.error,
    colorInfo: BRAND.info,
    colorLink: BRAND.primary,
    colorLinkHover: BRAND.primaryHover,
    colorLinkActive: BRAND.primaryActive,
    borderRadius: 8,
    borderRadiusSM: 6,
    borderRadiusLG: 12,
    fontFamily: FONT_FAMILY,
    fontSize: 14,
    colorBgLayout: BRAND.bgLayout,
    colorBgContainer: BRAND.bgContainer,
    colorText: BRAND.text,
    colorTextSecondary: BRAND.textSecondary,
    colorTextTertiary: BRAND.muted,
    colorTextPlaceholder: BRAND.muted,
    colorBorder: BRAND.border,
    colorBorderSecondary: BRAND.border,
    colorSplit: BRAND.border,
    controlOutline: 'rgba(31, 99, 245, 0.18)',
    controlOutlineWidth: 3,
    boxShadow: '0 8px 24px rgba(10, 10, 10, 0.08), 0 2px 6px rgba(10, 10, 10, 0.04)',
    boxShadowSecondary: '0 8px 24px rgba(10, 10, 10, 0.08), 0 2px 6px rgba(10, 10, 10, 0.04)',
    boxShadowTertiary: '0 1px 2px rgba(10, 10, 10, 0.04), 0 0 0 1px rgba(10, 10, 10, 0.02)',
  },
  components: {
    Layout: { siderBg: '#FFFFFF', headerBg: '#FFFFFF', headerHeight: 56, headerPadding: '0 16px', bodyBg: BRAND.bgLayout, triggerBg: '#FFFFFF' },
    Menu: {
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      itemColor: '#404040',
      itemHoverBg: '#F5F5F5',
      itemHoverColor: BRAND.text,
      itemSelectedBg: BRAND.ghost,
      itemSelectedColor: '#1747C7',
      groupTitleColor: BRAND.muted,
      itemHeight: 38,
      itemMarginBlock: 2,
      itemBorderRadius: 8,
      iconSize: 16,
    },
    // Toasts sit below Drawer/Modal (1000) so they never cover drawer header actions (Acknowledge / Close alert).
    Notification: { zIndexPopup: 950 },
    Table: {
      headerBg: BRAND.headerBg,
      headerColor: BRAND.textSecondary,
      headerSplitColor: 'transparent',
      rowHoverBg: '#FAFAFA',
      rowSelectedBg: '#F3F7FF',
      rowSelectedHoverBg: '#EAF1FF',
      borderColor: BRAND.border,
      cellPaddingBlockMD: 12,
    },
    Card: { colorBorderSecondary: BRAND.border, paddingLG: 20, headerFontSize: 15, borderRadiusLG: 12 },
    Button: { controlHeight: 36, controlHeightSM: 32, controlHeightLG: 40, fontWeight: 500, defaultShadow: 'none', primaryShadow: 'none', dangerShadow: 'none', defaultBorderColor: BRAND.border },
    Input: { controlHeight: 36, activeShadow: '0 0 0 3px rgba(31, 99, 245, 0.18)' },
    Select: { controlHeight: 36, optionSelectedBg: BRAND.ghost },
    DatePicker: { controlHeight: 36 },
    Tag: { borderRadiusSM: 999 },
    Drawer: { paddingLG: 20 },
    Modal: { borderRadiusLG: 16 },
    Statistic: { titleFontSize: 13, contentFontSize: 26 },
    Tabs: { titleFontSize: 14, horizontalItemGutter: 24 },
    Segmented: { trackBg: '#F5F5F5', itemSelectedBg: '#FFFFFF', trackPadding: 3 },
    Switch: { trackHeight: 22, handleSize: 18 },
    Pagination: { itemActiveBg: '#FFFFFF' },
    Tooltip: { colorBgSpotlight: '#171717' },
  },
};
