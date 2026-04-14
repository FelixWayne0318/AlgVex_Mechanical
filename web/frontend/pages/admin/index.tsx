"use client";

import { useEffect } from "react";
import { useRouter } from "next/router";
import Head from "next/head";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Admin Login Page
 *
 * This page is kept simple without any browser-only dependencies (framer-motion, etc.)
 * to ensure SSR works correctly. The dashboard with animations is in a separate file.
 */
export default function AdminLoginPage() {
  const router = useRouter();

  useEffect(() => {
    // Check if already authenticated
    const urlToken = router.query.token as string;
    const storedToken = localStorage.getItem("admin_token");

    if (urlToken) {
      // Token from OAuth callback - store and redirect to dashboard
      localStorage.setItem("admin_token", urlToken);
      router.replace("/admin/dashboard");
    } else if (storedToken) {
      // Already have token - verify and redirect
      verifyTokenAndRedirect(storedToken);
    }
  }, [router.query.token]);

  const verifyTokenAndRedirect = async (token: string) => {
    try {
      const res = await fetch("/api/auth/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        router.replace("/admin/dashboard");
      } else {
        // Token invalid - clear it
        localStorage.removeItem("admin_token");
      }
    } catch {
      // Network error - let user try login
    }
  };

  const handleLogin = () => {
    window.location.href = "/api/auth/login";
  };

  return (
    <>
      <Head>
        <title>Admin Login - AlgVex</title>
      </Head>
      <div className="min-h-screen gradient-bg flex items-center justify-center px-4">
        <Card className="w-full max-w-md border-border/50">
          <CardHeader className="text-center">
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-primary flex items-center justify-center">
              <span className="text-primary-foreground font-bold text-2xl">A</span>
            </div>
            <CardTitle className="text-2xl">Admin Login</CardTitle>
            <p className="text-sm text-muted-foreground mt-2">
              Sign in to access the admin dashboard
            </p>
          </CardHeader>
          <CardContent>
            <Button onClick={handleLogin} className="w-full" size="lg">
              <svg className="w-5 h-5 mr-2" viewBox="0 0 24 24">
                <path
                  fill="currentColor"
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                />
                <path
                  fill="currentColor"
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                />
                <path
                  fill="currentColor"
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                />
                <path
                  fill="currentColor"
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                />
              </svg>
              Sign in with Google
            </Button>
            <p className="text-xs text-muted-foreground text-center mt-4">
              Only authorized administrators can access this panel
            </p>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
