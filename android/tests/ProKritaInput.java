// SPDX-License-Identifier: GPL-3.0-or-later
// Shell-only input driver for an explicitly opened scratch canvas. Not an APK.
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.MotionEvent;
import java.lang.reflect.Method;

public final class ProKritaInput {
    private final Object manager;
    private final Method inject;
    private final Method displaySetter;
    private final int display;
    private long downTime;
    private boolean mouse;

    private ProKritaInput(int display) throws Exception {
        this.display = display;
        Class<?> type;
        try { type = Class.forName("android.hardware.input.InputManagerGlobal"); }
        catch (ClassNotFoundException oldAndroid) { type = Class.forName("android.hardware.input.InputManager"); }
        manager = type.getMethod("getInstance").invoke(null);
        inject = type.getMethod("injectInputEvent", InputEvent.class, int.class);
        displaySetter = InputEvent.class.getDeclaredMethod("setDisplayId", int.class);
        displaySetter.setAccessible(true);
    }

    private void event(int action, int count, float x, float y, boolean pen) throws Exception {
        MotionEvent.PointerProperties[] properties = new MotionEvent.PointerProperties[count];
        MotionEvent.PointerCoords[] coords = new MotionEvent.PointerCoords[count];
        for (int i = 0; i < count; i++) {
            properties[i] = new MotionEvent.PointerProperties();
            properties[i].id = i;
            properties[i].toolType = pen ? (mouse ? MotionEvent.TOOL_TYPE_MOUSE : MotionEvent.TOOL_TYPE_STYLUS) : MotionEvent.TOOL_TYPE_FINGER;
            coords[i] = new MotionEvent.PointerCoords();
            coords[i].x = x + i * 55;
            coords[i].y = y;
            coords[i].pressure = action == MotionEvent.ACTION_UP ? 0 : .7f;
            coords[i].size = pen ? .01f : .1f;
        }
        MotionEvent event = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(), action,
            count, properties, coords, 0, mouse && action != MotionEvent.ACTION_UP ? MotionEvent.BUTTON_PRIMARY : 0, 1, 1, pen && !mouse ? 4 : 6, 0,
            pen ? (mouse ? InputDevice.SOURCE_MOUSE : InputDevice.SOURCE_STYLUS | InputDevice.SOURCE_TOUCHSCREEN) : InputDevice.SOURCE_TOUCHSCREEN, 0);
        try {
            displaySetter.invoke(event, display);
            if (!Boolean.TRUE.equals(inject.invoke(manager, event, 2)))
                throw new IllegalStateException("Android rejected input injection");
        } finally { event.recycle(); }
    }

    private void tap(int count, float x, float y) throws Exception {
        downTime = SystemClock.uptimeMillis();
        event(MotionEvent.ACTION_DOWN, 1, x, y, false);
        for (int i = 1; i < count; i++) {
            SystemClock.sleep(20);
            event(MotionEvent.ACTION_POINTER_DOWN | (i << MotionEvent.ACTION_POINTER_INDEX_SHIFT), i + 1, x, y, false);
        }
        SystemClock.sleep(55);
        for (int i = count - 1; i > 0; i--) {
            event(MotionEvent.ACTION_POINTER_UP | (i << MotionEvent.ACTION_POINTER_INDEX_SHIFT), i + 1, x, y, false);
            SystemClock.sleep(20);
        }
        event(MotionEvent.ACTION_UP, 1, x, y, false);
    }

    private void circle(float x, float y) throws Exception {
        downTime = SystemClock.uptimeMillis();
        final float radius = 100;
        event(MotionEvent.ACTION_DOWN, 1, x + radius, y, true);
        for (int i = 1; i <= 72; i++) {
            double angle = 2 * Math.PI * i / 72;
            event(MotionEvent.ACTION_MOVE, 1, x + radius * (float)Math.cos(angle),
                y + .84f * radius * (float)Math.sin(angle), true);
            SystemClock.sleep(12);
        }
        SystemClock.sleep(900); // native hold-to-snap threshold is 550 ms
        event(MotionEvent.ACTION_UP, 1, x + radius, y, true);
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 4) throw new IllegalArgumentException("display tap2|tap3|tap4|circle x y; use only a scratch canvas");
        ProKritaInput driver = new ProKritaInput(Integer.parseInt(args[0]));
        float x = Float.parseFloat(args[2]), y = Float.parseFloat(args[3]);
        if (args[1].equals("circle") || args[1].equals("mousecircle")) {
            driver.mouse = args[1].equals("mousecircle");
            driver.circle(x, y);
        }
        else if (args[1].equals("doubletap")) {driver.tap(1,x,y);SystemClock.sleep(100);driver.tap(1,x,y);}
        else if (args[1].matches("tap[1234]")) driver.tap(Integer.parseInt(args[1].substring(3)), x, y);
        else throw new IllegalArgumentException("Unknown input test");
        System.out.println("Injected " + args[1] + " on display " + args[0]);
    }
}
