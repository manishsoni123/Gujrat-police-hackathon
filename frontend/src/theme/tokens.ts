import type { ThemeConfig } from 'antd';
import { BRAND } from './colours';

export const FONT_FAMILY =
  '"Inter", -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif';
export const MONO_FAMILY = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

/** Ant Design 5 theme exactly per CONTRACT §11.3. */
export const antTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorSuccess: BRAND.success,
    colorWarning: BRAND.warning,
    colorError: BRAND.error,
    colorInfo: BRAND.info,
    borderRadius: 8,
    fontFamily: FONT_FAMILY,
    fontSize: 14,
    colorBgLayout: BRAND.bgLayout,
    colorBgContainer: BRAND.bgContainer,
    colorText: BRAND.text,
    colorTextSecondary: BRAND.textSecondary,
    colorBorder: BRAND.border,
    colorBorderSecondary: BRAND.border,
    boxShadowTertiary: '0 1px 2px rgba(16, 24, 40, 0.06)',
  },
  components: {
    Layout: { siderBg: BRAND.navy, headerBg: '#FFFFFF', headerHeight: 56, headerPadding: '0 16px', bodyBg: BRAND.bgLayout },
    Menu: {
      darkItemBg: BRAND.navy,
      darkSubMenuItemBg: BRAND.navy,
      darkItemSelectedBg: BRAND.primary,
      darkItemColor: 'rgba(255,255,255,0.78)',
      darkItemHoverColor: '#FFFFFF',
      darkItemSelectedColor: '#FFFFFF',
      darkGroupTitleColor: 'rgba(255,255,255,0.45)',
      itemHeight: 38,
      itemMarginBlock: 2,
      iconSize: 16,
    },
    // Toasts sit below Drawer/Modal (1000) so they never cover drawer header actions (Acknowledge / Close alert).
    Notification: { zIndexPopup: 950 },
    Table: { headerBg: BRAND.headerBg, headerColor: BRAND.text, rowHoverBg: '#F8FAFF', cellPaddingBlockMD: 10 },
    Card: { colorBorderSecondary: BRAND.border, paddingLG: 20, headerFontSize: 15 },
    Button: { controlHeight: 36, controlHeightSM: 28, fontWeight: 500 },
    Input: { controlHeight: 36 },
    Select: { controlHeight: 36 },
    DatePicker: { controlHeight: 36 },
    Tag: { borderRadiusSM: 6 },
    Drawer: { paddingLG: 20 },
    Statistic: { titleFontSize: 13, contentFontSize: 26 },
    Tabs: { titleFontSize: 14 },
  },
};
