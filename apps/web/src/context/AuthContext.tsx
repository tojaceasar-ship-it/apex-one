import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import {
  apiFetch,
  clearStoredSession,
  loadStoredSession,
  saveStoredSession,
} from "../api";
import { Locale, TRANSLATIONS } from "../i18n";

export type WorkspaceScope = "all" | "research" | "demo";

export interface UserOrgSummary {
  organization_id: string;
  slug: string;
  name: string;
  role: string;
  emergency_stop_active: boolean;
}

interface AuthContextValue {
  loading: boolean;
  user: any | null;
  role: string;
  organizations: UserOrgSummary[];
  activeOrgId: string | null;
  scope: WorkspaceScope;
  locale: Locale;
  t: (typeof TRANSLATIONS)["pl"] | (typeof TRANSLATIONS)["en"];
  setScope: (s: WorkspaceScope) => void;
  setLocale: (l: Locale) => void;
  switchOrganization: (orgId: string) => Promise<void>;
  loginWithCredentials: (email: string, password: string) => Promise<void>;
  registerAccount: (payload: {
    email: string;
    password: string;
    display_name: string;
    organization_name: string;
    locale: Locale;
  }) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<any | null>(null);
  const [role, setRole] = useState<string>("Viewer");
  const [organizations, setOrganizations] = useState<UserOrgSummary[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(
    loadStoredSession().organizationId
  );
  const [scope, setScope] = useState<WorkspaceScope>("all");
  const [locale, setLocaleState] = useState<Locale>("pl");

  const syncProfilePayload = useCallback((me: any) => {
    const stored = loadStoredSession();
    setUser(me.user);
    const activeId = me.active_organization_id || stored.organizationId;
    const orgList: UserOrgSummary[] = me.organizations || [];
    const matchedOrg = orgList.find((o) => o.organization_id === activeId) || orgList[0];
    setRole(me.role || matchedOrg?.role || "Owner");
    setOrganizations(orgList);
    setActiveOrgId(activeId);
    if (me.user?.locale === "en" || me.user?.locale === "pl") {
      setLocaleState(me.user.locale);
    }
    saveStoredSession({
      ...stored,
      organizationId: activeId,
    });
  }, []);

  const refreshProfile = useCallback(async () => {
    const stored = loadStoredSession();
    if (!stored.token) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await apiFetch("/api/v1/auth/me");
      syncProfilePayload(me);
    } catch {
      clearStoredSession();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, [syncProfilePayload]);

  useEffect(() => {
    void refreshProfile();
  }, [refreshProfile]);

  const loginWithCredentials = async (email: string, password: string) => {
    const res = await apiFetch("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    saveStoredSession({
      token: res.access_token,
      csrfToken: res.csrf_token,
      organizationId: res.active_organization_id,
    });
    // Populate user profile immediately from login response so navigation to "/" succeeds
    syncProfilePayload(res);
    setLoading(false);

    // Confirm session round-trip; surface error explicitly if proxy/session check fails
    try {
      const me = await apiFetch("/api/v1/auth/me");
      syncProfilePayload(me);
    } catch (err) {
      clearStoredSession();
      setUser(null);
      throw err;
    }
  };

  const registerAccount = async (payload: {
    email: string;
    password: string;
    display_name: string;
    organization_name: string;
    locale: Locale;
  }) => {
    await apiFetch("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loginWithCredentials(payload.email, payload.password);
  };

  const switchOrganization = async (orgId: string) => {
    const stored = loadStoredSession();
    saveStoredSession({ ...stored, organizationId: orgId });
    setActiveOrgId(orgId);
    await refreshProfile();
  };

  const setLocale = (nextLocale: Locale) => {
    setLocaleState(nextLocale);
    if (user) {
      void apiFetch("/api/v1/auth/preferences", {
        method: "PATCH",
        body: JSON.stringify({ locale: nextLocale }),
      }).catch(() => {});
    }
  };

  const logout = async () => {
    try {
      await apiFetch("/api/v1/auth/logout", { method: "POST" });
    } catch {
      // ignore
    }
    clearStoredSession();
    setUser(null);
    setOrganizations([]);
    setActiveOrgId(null);
  };

  return (
    <AuthContext.Provider
      value={{
        loading,
        user,
        role,
        organizations,
        activeOrgId,
        scope,
        locale,
        t: TRANSLATIONS[locale],
        setScope,
        setLocale,
        switchOrganization,
        loginWithCredentials,
        registerAccount,
        logout,
        refreshProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
