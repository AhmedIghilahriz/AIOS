"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function TestRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/dashboard"); }, [router]);
  return (
    <div className="min-h-screen flex items-center justify-center text-gray-500">
      Redirection...
    </div>
  );
}
