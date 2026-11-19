// SPDX-License-Identifier: GPL-3.0-or-later
package org.krita.android;

import android.app.Activity;
import android.app.Dialog;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

/** Keep a focusable input window while Qt creates its first native window. */
final class ProKritaStartup {
    static void show(Activity activity) {
        Dialog loading = new Dialog(activity);
        loading.setCancelable(false);
        LinearLayout content = new LinearLayout(activity);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = (int)(24 * activity.getResources().getDisplayMetrics().density);
        content.setPadding(padding,padding,padding,padding);
        content.addView(new ProgressBar(activity));
        TextView label = new TextView(activity);
        label.setText("Loading ProKrita…");label.setTextSize(18);label.setPadding(0,padding,0,0);
        content.addView(label);loading.setContentView(content);loading.show();
        loading.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE);
        Handler handler = new Handler(Looper.getMainLooper());
        handler.post(new Runnable() {
            @Override public void run() {
                if(activity.isFinishing() || activity.isDestroyed()) {loading.dismiss();return;}
                boolean ready = false;
                try {ready = JNIWrappers.hasMainWindowLoaded();} catch(UnsatisfiedLinkError ignored) { }
                if(ready) handler.postDelayed(loading::dismiss,750);
                else handler.postDelayed(this,100);
            }
        });
    }
}
