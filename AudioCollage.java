public class AudioCollage {

    // Returns a new array that rescales a[] by a factor of alpha.
    public static double[] amplify(double[] a, double alpha)

    // Returns a new array that replaces samples > +1.0 with +1.0
   //  and samples < -1.0 with -1.0.  Other samples are unchanged.
    public static double[] clamp(double[] a)

    // Returns a new array that is the reverse of a[].
    public static double[] reverse(double[] a)

    // Returns a new array that is the concatenation of a[] and b[].
    public static double[] merge(double[] a, double[] b)

    // Returns a new array that is the sum of a[] and b[],
    // padding the shorter array with trailing 0s if necessary.
    public static double[] mix(double[] a, double[] b)

    // Returns a new array that changes the speed of a[] by a factor of alpha.
    public static double[] changeSpeed(double[] a, double alpha)

    // Creates an audio collage and plays it on standard audio.
    // See below for the requirements.
    public static void main(String[] args)

}